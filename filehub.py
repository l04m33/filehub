import asyncio
import logging
import argparse
import json
import urllib.parse as urlparse
import os
import traceback
from pyx import http
from pyx import io
from pyx.log import logger


shelf = {}


class TransferIncompleteError(Exception): pass


class TransportEntry:
    def __init__(self, name, content_type, content_length, reader, loop=None):
        self.name = name
        self.content_type = content_type
        self.content_length = content_length
        self.reader = reader
        self.loop = loop or asyncio.get_event_loop()
        self.done = asyncio.Future(loop=self.loop)


class RootResource(http.UrlResource):
    def __init__(self, index_page):
        super().__init__()
        self._page_content = index_page

    def get_child(self, key):
        if key == 'hub':
            return HubResource()
        elif key == 'list':
            return ListResource()

        raise http.HttpError(404, '{} not found'.format(repr(key)))

    @http.methods(['GET'])
    @asyncio.coroutine
    def handle_request(self, req):
        resp = req.respond(200)
        resp.headers.append(
            http.HttpHeader('Content-Length', len(self._page_content)))
        resp.headers.append(
            http.HttpHeader('Content-Type', 'text/html'))
        yield from resp.send()
        yield from resp.send_body(self._page_content)


class HubResource(http.UrlResource):
    def get_child(self, key):
        try:
            int_key = int(key, 10)
        except ValueError:
            raise http.HttpError(404, '{} not found'.format(repr(key)))
        return RecvResource(int_key)

    def _get_shelf_entry_idx(self, req):
        if not req.query:
            raise http.HttpError(400, 'No query string')

        q = urlparse.parse_qs(req.query)
        if 'e' not in q:
            raise http.HttpError(400, 'Parameter `e` is required')

        try:
            int_e = int(q['e'][0], 10)
        except ValueError:
            raise http.HttpError(400, 'Parameter `e` should be an integer')

        if int_e not in shelf:
            raise http.HttpError(404, 'Entry {} not found'.format(int_e))

        return int_e

    def _post_part_cb(self, headers, breader, lreader, boundary, resp):
        disp = http.get_first_kv(headers, 'Content-Disposition')
        disp_list = disp.split(';')

        file_name = 'Anonymous File'
        field_name = None
        for d in disp_list:
            sd = d.strip()
            if sd.startswith('name='):
                field_name = sd[5:]
            elif sd.startswith('filename='):
                file_name = sd[9:]
                if len(file_name) >= 2 \
                        and file_name[0] == '"' \
                        and file_name[-1] == '"':
                    file_name = file_name[1:-1]
                    if not file_name:
                        file_name = 'Anonymous File'

        if field_name == '"userfile"':
            part_ct = http.get_first_kv(headers, 'Content-Type')
            file_len = \
                lreader._remaining - \
                    (len(boundary) + len(b'--') * 2 + len(b'\r\n') * 2)
            new_entry = \
                TransportEntry(file_name, part_ct, file_len, breader)
            new_idx = \
                resp.connection.writer.get_extra_info('socket').fileno()

            shelf[new_idx] = new_entry
            yield from new_entry.done

    @http.methods(['GET'])
    @asyncio.coroutine
    def handle_request(self, req):
        entry_idx = self._get_shelf_entry_idx(req)
        entry = shelf[entry_idx]

        resp = req.respond(303)
        resp.headers.append(
            http.HttpHeader('Location', 'hub/{}/{}'.format(
                                entry_idx, urlparse.quote(entry.name))))
        resp.headers.append(http.HttpHeader('Content-Length', 0))
        yield from resp.send()

    @handle_request.methods(['POST'])
    @asyncio.coroutine
    def handle_post(self, req):
        ct = req.get_first_header('Content-Type')
        clen_str = req.get_first_header('Content-Length')
        try:
            clen = int(clen_str, 10)
        except ValueError:
            clen = -1
        boundary = self._parse_boundary(ct).encode()

        if not boundary or clen < len(boundary):
            raise http.HttpError(400, 'Bad Content-Length/Content-Type')

        logger('HubResource').debug('content-length = %r, boundary = %r',
                                    clen, boundary)

        resp = req.respond(200)
        resp.headers.append(http.HttpHeader('Content-Length', 5))
        resp.headers.append(http.HttpHeader('Content-Type', 'text/plain'))
        yield from resp.send()

        lreader = \
            io.LengthReader(io.BufferedReader(resp.connection.reader), clen)

        @asyncio.coroutine
        def part_cb(h, br):
            yield from self._post_part_cb(h, br, lreader, boundary, resp)

        try:
            yield from http.parse_multipart_formdata(lreader, boundary, part_cb)
        except Exception as exc:
            logger('HubResource').debug(traceback.format_exc())
            logger('HubResource').debug(
                'Transfer failed: %r, closing sender connection', exc)
            resp.connection.close()
            return

        yield from resp.send_body(b'Done.')

    def _parse_boundary(self, content_type):
        ct_prefix = "multipart/form-data;"
        bd_prefix = "boundary="

        if content_type is None:
            return None

        lower_ct = content_type.lower()
        if not lower_ct.startswith(ct_prefix):
            return None

        bd_idx = lower_ct.find(bd_prefix, len(ct_prefix))
        if bd_idx < 0:
            return None

        bd_idx += len(bd_prefix)
        return content_type[bd_idx:].strip()


class RecvResource(http.UrlResource):
    def __init__(self, entry_idx):
        if entry_idx not in shelf:
            raise http.HttpError(404, 'Entry {} not found'.format(entry_idx))
        self._entry_idx = entry_idx

    def get_child(self, key):
        return self

    @http.methods(['GET'])
    @asyncio.coroutine
    def handle_request(self, req):
        entry = shelf[self._entry_idx]
        del shelf[self._entry_idx]

        resp = req.respond(200)
        resp.headers.append(http.HttpHeader('Content-Length', entry.content_length))
        if entry.content_type is not None:
            resp.headers.append(http.HttpHeader('Content-Type', entry.content_type))
        yield from resp.send()

        total_len = 0
        file_content = yield from entry.reader.read(8192)
        while len(file_content) > 0:
            total_len += len(file_content)

            try:
                yield from resp.send_body(file_content)
            except Exception as exc:
                logger('RecvResource').debug(traceback.format_exc())
                logger('RecvResource').debug('Transfer failed: %r', exc)
                entry.done.set_exception(exc)
                resp.connection.close()
                return

            file_content = yield from entry.reader.read(8192)

        logger('RecvResource').debug(
            'entry_idx = %r, transfer completed, '
            'total_len = %r, content-length = %r',
            self._entry_idx, total_len, entry.content_length)

        if total_len < entry.content_length:
            # The transfer was canceled on the sender side,
            # we tell the receiver then.
            resp.connection.close()
            entry.done.set_exception(
                TransferIncompleteError('Sender disconnected'))
        else:
            entry.done.set_result(None)


class ListResource(http.UrlResource):
    @http.methods(['GET'])
    @asyncio.coroutine
    def handle_request(self, req):
        jobj_list = []
        for idx, entry in shelf.items():
            jobj_list.append({
                'id': idx,
                'name': entry.name,
                'size': entry.content_length,
                'type': entry.content_type,
                'url': 'hub?e={}'.format(idx),
            })
        jstr = json.dumps({'fileList': jobj_list})

        logger('ListResource').debug('jstr = %r', jstr)

        resp = req.respond(200)
        resp.headers.append(http.HttpHeader('Content-Length', len(jstr)))
        resp.headers.append(http.HttpHeader('Content-Type', 'application/json'))
        yield from resp.send()
        yield from resp.send_body(jstr)


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-b', '--bind',
                        help='Specify bind address (default: all interfaces)',
                        default='',
                        type=str)
    parser.add_argument('-p', '--port',
                        help='Which port to listen (default: 8000)',
                        default=8000,
                        type=int)
    parser.add_argument('--backlog',
                        help='Backlog for the listening socket (default: 128)',
                        default=128,
                        type=int)
    parser.add_argument('--loglevel',
                        help='Log level (default: info)',
                        default='info',
                        type=str,
                        choices=[
                            'critical', 'fatal', 'error',
                            'warning', 'info', 'debug',
                        ])

    return parser.parse_args()


def main():
    args = parse_arguments()

    logging.basicConfig(level=args.loglevel.upper())

    loop = asyncio.get_event_loop()

    cur_file = os.path.abspath(__file__)
    home_dir, _filename = os.path.split(cur_file)
    ui_file = os.path.join(home_dir, 'ui.html')
    with open(ui_file, 'rb') as f:
        ui_page = f.read()

    def root_factory(req):
        return RootResource(ui_page)

    req_cb = http.HttpRequestCB(root_factory)
    conn_cb = http.HttpConnectionCB(req_cb)

    starter = asyncio.start_server(conn_cb, args.bind, args.port,
                                   backlog=args.backlog,
                                   reuse_address=True,
                                   loop=loop)
    server = loop.run_until_complete(starter)

    if args.bind == '':
        logger().info('Server serving at <all interfaces>:{}'.format(args.port))
    else:
        logger().info('Server serving at {}:{}'.format(args.bind, args.port))

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass

    server.close()
    loop.run_until_complete(server.wait_closed())
    loop.close()


if __name__ == '__main__':
    main()
