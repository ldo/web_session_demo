#!/usr/bin/python3
#+
# Try creating a web page that keeps a WebSocket connection alive, so
# the server can detect the moment the page is closed (to within the
# chosen ping interval). This module is intended to be invoked via
# some ASGI framework, e.g. using uvicorn:
#
#   uvicorn --port 6502 detect_page_closed:main
#
# then watch the info-level log messages as you open
# <http://127.0.0.1:6502/> in your browser, leave it open for a few
# seconds, and then close the page. With a ping interval of 1 second,
# it should report the disconnection within 1 second of the page
# closing.
#
# ASGI specification: <https://asgi.readthedocs.io/en/latest/specs/index.html>.
#
# You can control the logging level via the LOGLEVEL environment
# variable. E.g. set LOGLEVEL=10 to enable full debugging messages.
# For more info about Python’s logging module, see
# <https://docs.python.org/3/library/logging.html>.
#
# Copyright 2023 by Lawrence D'Oliveiro <ldo@geek-central.gen.nz>. This
# script is licensed CC0
# <https://creativecommons.org/publicdomain/zero/1.0/>; do with it
# what you will.
#-

import sys
import os
import enum
import io
import asyncio
import logging

#+
# Useful stuff
#-

class WEBSOCK_CLOSE(enum.IntEnum) :
    "some useful WebSocket codes to use on connection close."
    # codes come from RFC6455 <https://www.rfc-editor.org/rfc/rfc6455>
    NORMAL = 1000
    PROTOCOL_ERROR = 1002
    DATA_ERROR = 1003
    WTF = 1011 # Weird Technical Failure
#end WEBSOCK_CLOSE

#+
# Logging
#-

LOGGING_NAME = "detect_page_closed"
  # identifies my messages in the logging module

def get_logger() :
    "retrieves the global logger instance, suitably configuring" \
    " it as necessary."
    logger = logging.getLogger(LOGGING_NAME)
    try :
        loglevel = int(os.getenv("LOGLEVEL", ""))
        if loglevel < 0 :
            raise ValueError("invalid loglevel")
        #end if
    except ValueError :
        loglevel = logging.INFO
    #end try
    if loglevel != None :
        logger.setLevel(loglevel)
    #end if
    if not logger.hasHandlers() :
        # uvicorn CLI command seems to quietly ignore my log messages,
        # so I insert my own logging setup to get around this.
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(logging.Formatter(fmt = "%(levelname)-8s: %(message)s"))
        logger.addHandler(stderr_handler)
        logger.propagate = False
    #end if
    return \
        logger
#end get_logger

#+
# Mainline
#-

SERVER_PORT = 6502
  # ensure this matches the port you told uvicorn to use to run me
PING_INTERVAL = 1.0 # seconds
  # adjust to taste

async def handle_http(scope, receive, send, logger) :
    "handles an HTTP connection."
    while True :
        event = await receive()
        logger.debug("http got event %s" % repr(event))
        if event["type"] == "http.request" :
            reply = io.BytesIO()
            out = io.TextIOWrapper(reply, encoding = "utf-8")
            out.write \
              (
                    "<!doctype html>\n"
                    "<head>\n"
                    "<title>Page Closure Detection Demo</title>\n"
                    "</head>\n"
                    "<body>\n"
                    "<script type=\"module\">\n"
                    "let conn = null\n"
                    "\n"
                    "function send_ping()\n"
                    "  {\n"
                    "    conn.send(\"pingy-pingy\")\n"
                    "  } /*send_ping*/\n"
                    "\n"
                    "function start_ping()\n"
                    "  {\n"
                    "    conn = new WebSocket(\"ws://127.0.0.1:%(server_port)d/r_u_thair\")\n"
                    "      /* actually path part of URL is ignored by my Python code */\n"
                    "    conn.onopen = send_ping\n"
                    "    conn.onmessage =\n"
                    "        function (evt)\n"
                    "          {\n"
                    "            console.log(\"ping response = \", evt.data)\n"
                    "            setTimeout(send_ping, %(ping_ms)d)\n"
                    "          } /*function*/\n"
                    "  /* don’t bother with conn.onclose */\n"
                    "  } /*start_ping*/\n"
                    "\n"
                    "start_ping()\n"
                    "</script>\n"
                    "<p>Feel free to close this very boring page at any time.\n"
                    "</body>\n"
                    "</html>\n"
                %
                    {
                        "server_port" : SERVER_PORT,
                        "ping_ms" : round(PING_INTERVAL * 1000),
                    }
              )
            out.flush()
            reply = reply.getvalue()
            await send \
              (
                {
                    "type" : "http.response.start",
                    "status" : 200,
                    "headers" :
                        [
                            ["content-type", "text/html; charset=utf-8"],
                            ["content-length", "%d" % len(reply)],
                        ],
                }
              )
            await send({"type" : "http.response.body", "body" : reply})
        elif event["type"] == "http.disconnect" :
            logger.info("http disconnect")
            break
        else :
            logger.warning("unexpected http event type %s" % repr(event["type"]))
        #end if
    #end while
#end handle_http

async def handle_websocket(scope, receive, send, logger) :
    "handles a WebSocket connection."
    while True :
        event = await receive()
        logger.debug("websocket got event %s" % repr(event))
        if event["type"] == "websocket.connect" :
            await send({"type" : "websocket.accept"})
        elif event["type"] == "websocket.receive" :
            # just echo back the data received
            reply = {"type" : "websocket.send"}
            for key in ("text", "bytes") :
                if key in event :
                    reply[key] = event[key]
                #end if
            #end for
            await send(reply)
            logger.info("websocket ping")
        elif event["type"] == "websocket.disconnect" :
            logger.info("websocket disconnect code %d" % event["code"])
            break
        else :
            logger.warning("unexpected websocket event type %s" % repr(event["type"]))
        #end if
    #end while
#end handle_websocket

async def main(scope, receive, send) :
    logger = get_logger()
    logger.debug("connection scope = %s" % repr(scope))
    if scope["type"] == "http" :
        await handle_http(scope, receive, send, logger)
    elif scope["type"] == "websocket" :
        await handle_websocket(scope, receive, send, logger)
    else :
        raise AssertionError \
          (
            "unrecognized scope type %s, must be “http” or “websocket”" % scope["type"]
          )
    #end if
#end main
