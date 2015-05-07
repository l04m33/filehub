import asyncio
import traceback
import json
import urllib.parse as urlparse
import pyx
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


class RootResource(pyx.UrlResource):
    def __init__(self, index_page):
        super().__init__()
        self._page_content = index_page

    def get_child(self, key):
        if key == 'hub':
            return HubResource()
        elif key == 'list':
            return ListResource()

        raise pyx.HttpError(404, '{} not found'.format(repr(key)))

    @pyx.methods(['GET'])
    @asyncio.coroutine
    def handle_request(self, req):
        resp = req.respond(200)
        resp.headers.append(
            pyx.HttpHeader('Content-Length', len(self._page_content)))
        resp.headers.append(
            pyx.HttpHeader('Content-Type', 'text/html'))
        yield from resp.send()
        yield from resp.send_body(self._page_content)


class HubResource(pyx.UrlResource):
    def get_child(self, key):
        try:
            int_key = int(key, 10)
        except ValueError:
            raise pyx.HttpError(404, '{} not found'.format(repr(key)))
        return RecvResource(int_key)

    def _get_shelf_entry_idx(self, req):
        if not req.query:
            raise pyx.HttpError(400, 'No query string')

        q = urlparse.parse_qs(req.query)
        if 'e' not in q:
            raise pyx.HttpError(400, 'Parameter `e` is required')

        try:
            int_e = int(q['e'][0], 10)
        except ValueError:
            raise pyx.HttpError(400, 'Parameter `e` should be an integer')

        if int_e not in shelf:
            raise pyx.HttpError(404, 'Entry {} not found'.format(int_e))

        return int_e

    def _post_part_cb(self, headers, breader, lreader, boundary, resp):
        disp = pyx.get_first_kv(headers, 'Content-Disposition')
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

        if field_name == '"f"':
            part_ct = pyx.get_first_kv(headers, 'Content-Type')
            file_len = \
                lreader._remaining - \
                    (len(boundary) + len(b'--') * 2 + len(b'\r\n') * 2)
            new_entry = \
                TransportEntry(file_name, part_ct, file_len, breader)
            new_idx = \
                resp.connection.writer.get_extra_info('socket').fileno()

            shelf[new_idx] = new_entry
            yield from new_entry.done

    @pyx.methods(['GET'])
    @asyncio.coroutine
    def handle_request(self, req):
        entry_idx = self._get_shelf_entry_idx(req)
        entry = shelf[entry_idx]

        resp = req.respond(303)
        resp.headers.append(
            pyx.HttpHeader('Location', 'hub/{}/{}'.format(
                                entry_idx, urlparse.quote(entry.name))))
        resp.headers.append(pyx.HttpHeader('Content-Length', 0))
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
            raise pyx.HttpError(400, 'Bad Content-Length/Content-Type')

        logger('filehub.HubResource').debug(
            'content-length = %r, boundary = %r', clen, boundary)

        resp = req.respond(200)
        resp.headers.append(pyx.HttpHeader('Content-Length', 5))
        resp.headers.append(pyx.HttpHeader('Content-Type', 'text/plain'))
        yield from resp.send()

        lreader = \
            pyx.LengthReader(pyx.BufferedReader(resp.connection.reader), clen)

        @asyncio.coroutine
        def part_cb(h, br):
            yield from self._post_part_cb(h, br, lreader, boundary, resp)

        try:
            yield from pyx.parse_multipart_formdata(lreader, boundary, part_cb)
        except Exception as exc:
            logger('filehub.HubResource').debug(traceback.format_exc())
            logger('filehub.HubResource').debug(
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


class RecvResource(pyx.UrlResource):
    def __init__(self, entry_idx):
        if entry_idx not in shelf:
            raise pyx.HttpError(404, 'Entry {} not found'.format(entry_idx))
        self._entry_idx = entry_idx

    def get_child(self, key):
        return self

    @pyx.methods(['GET'])
    @asyncio.coroutine
    def handle_request(self, req):
        entry = shelf[self._entry_idx]
        del shelf[self._entry_idx]

        resp = req.respond(200)
        resp.headers.append(pyx.HttpHeader('Content-Length', entry.content_length))
        if entry.content_type is not None:
            resp.headers.append(pyx.HttpHeader('Content-Type', entry.content_type))
        yield from resp.send()

        total_len = 0
        try:
            file_content = yield from entry.reader.read(8192)
            while len(file_content) > 0:
                total_len += len(file_content)
                yield from resp.send_body(file_content)
                file_content = yield from entry.reader.read(8192)
        except Exception as exc:
            logger('filehub.RecvResource').debug(traceback.format_exc())
            logger('filehub.RecvResource').debug('Transfer failed: %r', exc)
            entry.done.set_exception(exc)
            resp.connection.close()
            return

        logger('filehub.RecvResource').debug(
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


class ListResource(pyx.UrlResource):
    @pyx.methods(['GET'])
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

        logger('filehub.ListResource').debug('jstr = %r', jstr)

        resp = req.respond(200)
        resp.headers.append(pyx.HttpHeader('Content-Length', len(jstr)))
        resp.headers.append(pyx.HttpHeader('Content-Type', 'application/json'))
        yield from resp.send()
        yield from resp.send_body(jstr)
