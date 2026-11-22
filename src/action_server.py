"""Single-process sandbox HTTP server backed by two local SQLite databases.
Binds loopback by default; use authenticated tunnels/controls if exposed on a LAN.
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from action_core import APIError, Engine
from common import canonical, read_json


def handler(engine: Engine):
    class RequestHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            try:
                auth_header = self.headers.get("Authorization", "")
                token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
                actor = engine.authenticate(token)

                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 0 or content_length > 1048576:
                    raise APIError(413, "invalid request size")

                body = json.loads(self.rfile.read(content_length)) if content_length else {}
                route = self.path

                if self.command == "GET" and route == "/snapshot":
                    out = engine.snapshot(actor)
                elif self.command == "GET" and route == "/anchor":
                    out = engine.anchor(actor)
                elif self.command == "POST" and route == "/proposals":
                    out = engine.propose(actor, body.get("decisions"))
                elif self.command == "POST" and route == "/review":
                    out = engine.review(
                        actor,
                        body.get("decision_id"),
                        body.get("approve"),
                        body.get("reason"),
                    )
                elif self.command == "POST" and route == "/control":
                    out = engine.pause(actor, body.get("paused"))
                elif self.command == "POST" and route == "/dispatch":
                    out = engine.dispatch(actor, body.get("fail_after_effect", False))
                else:
                    raise APIError(404, "unknown route")
                status = 200
            except APIError as err:
                status = err.status
                out = {"error": err.message}
            except (TypeError, ValueError, KeyError) as err:
                status = 400
                out = {"error": str(err)}
            except Exception:
                status = 500
                out = {"error": "internal service error"}

            raw = canonical(out).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = handle_request
        do_POST = handle_request

    return RequestHandler


def main():
    parser = argparse.ArgumentParser(description="Run the Lab 4 local sandbox Action Server.")
    parser.add_argument("--config", required=True, help="Path to service_config.json")
    parser.add_argument("--state", required=True, help="Directory for control/receiver SQLite DBs")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    args = parser.parse_args()

    engine = Engine(args.state, read_json(args.config))
    server = HTTPServer((args.host, args.port), handler(engine))
    print(f"Sandbox Action Server listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
