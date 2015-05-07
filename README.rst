###############
What's Filehub?
###############

What do you do if you want to quickly share a file in your local network? You
may want to:

1. Set up a file server, such as an FTP server, or

2. Install point-to-point file transfer software, such as IP Messenger,
   on all the machines you want to share files.

The first way needs you to wait for the shared file to be completely
uploaded before you can start downloading, if the file was not on the file
server already. And it also needs vast caching space on the file server.

The second way needs you to install the software on all the machines. It's
cumbersome indeed. And what if there's an incompatible platform that you
cannot install the software on?

Filehub solves all these problems. If you want to share a file quickly,
Filehub is the choice.

Filehub is actually an HTTP server that accepts file uploading via POST
methods. But it's different from a regular file server in that it relays the
uploaded data **directly** to the downloader. This means that you don't need
to wait for the upload to be completed before downloading.

#####
Usage
#####

Install Filehub:

.. code-block:: sh

    pip install filehub

Start it on your local server:

.. code-block:: sh

    filehub

Filehub listens on <all interfaces>:8000 by default. Now you can use any HTTP
client to access Filehub's interface. For example, uploading a file using
curl:

.. code-block:: sh

    curl --form f=@FILENAME http://ip.addr.for.filehub:8000/hub

Or you can also use Filehub's bundled HTML5 client at
http://ip.addr.for.filehub:8000 . It has a nicer look, and supports
drag & drop operations.

The files being uploaded will be shown in the bottom part of the HTML5 page.
Simply clicking the file name will start downloading.

##########
Advantages
##########

1. HTTP protocol is quite popular. Most machines and smart devices already
   have a client installed, you don't need other stuff.

2. Filehub does not cache any data at all, so it can be run on devices with
   limited resources, such as a Raspberry Pi or an NAS box, as long as you
   have Python installed on them.

3. Filehub streams the shared files on-the-fly, so you don't need to wait for
   the files to be uploaded first. This makes it  extremely suitable for
   one-shot sharing of large files.

#######
License
#######

Filehub is licensed under the terms of the MIT license. See LICENSE.txt.
