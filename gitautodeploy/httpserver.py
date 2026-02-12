from __future__ import absolute_import
from .events import WebhookAction
from .parsers import get_service_handler


def WebhookRequestHandlerFactory(config, event_store, server_status, is_https=False):
    """Factory method for webhook request handler class"""
    try:
        from SimpleHTTPServer import SimpleHTTPRequestHandler
    except ImportError as e:
        from http.server import SimpleHTTPRequestHandler

    class WebhookRequestHandler(SimpleHTTPRequestHandler, object):
        """Extends the BaseHTTPRequestHandler class and handles the incoming
        HTTP requests."""

        def __init__(self, *args, **kwargs):
            self._config = config
            self._event_store = event_store
            self._server_status = server_status
            self._is_https = is_https
            super(WebhookRequestHandler, self).__init__(*args, **kwargs)

        def end_headers(self):
            self.send_header('Access-Control-Allow-Origin', '*')
            SimpleHTTPRequestHandler.end_headers(self)

        def do_HEAD(self):

            # Web UI needs to be enabled
            if not self.validate_web_ui_enabled():
                return

            # Web UI might require HTTPS
            if not self.validate_web_ui_https():
                return

            # Client needs to be whitelisted
            if not self.validate_web_ui_whitelist():
                return

            # Client needs to authenticate
            if not self.validate_web_ui_basic_auth():
                return

            return SimpleHTTPRequestHandler.do_HEAD(self)

        def do_GET(self):

            # Web UI needs to be enabled
            if not self.validate_web_ui_enabled():
                return

            # Web UI might require HTTPS
            if not self.validate_web_ui_https():
                return

            # Client needs to be whitelisted
            if not self.validate_web_ui_whitelist():
                return

            # Client needs to authenticate
            if not self.validate_web_ui_basic_auth():
                return

            # Handle status API call
            if self.path == "/api/status":
                self.handle_status_api()
                return

            if self.path == "/api/github/sync":
                self.handle_github_sync_api()
                return

            if self.path == "/api/hf/check":
                self.handle_hf_check_api()
                return

            # Serve static file
            return SimpleHTTPRequestHandler.do_GET(self)

        def handle_status_api(self):
            import json
            from os import urandom
            from base64 import b64encode

            data = {
                'events': self._event_store.dict_repr(),
                'auth-key': self._server_status['auth-key']
            }

            data.update(self.get_server_status())

            self.send_response(200, 'OK')
            self.send_header('Content-type', 'application/json')
            self.end_headers()

            def default(obj):
                if isinstance(obj, bytes):
                    return obj.decode('utf-8')
                return str(obj)

            self.wfile.write(json.dumps(data, default=default).encode('utf-8'))

        def handle_github_sync_api(self):
            import json
            from .gitautodeploy import GitAutoDeploy

            success, msg = GitAutoDeploy().sync_github_repos()

            self.send_response(200, 'OK')
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "message": msg}).encode('utf-8'))

        def handle_hf_check_api(self):
            import json
            import os
            import requests

            # Prioritize HF_TOKEN as it's common in Spaces
            token = os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN') or os.environ.get('hf_token')
            space_id = os.environ.get('SPACE_ID', 'unknown')

            # Robust profile detection: try various common env vars used in HF Spaces
            hf_profile = os.environ.get('HF_PROFILE') or \
                         os.environ.get('HF_Profile') or \
                         os.environ.get('HF_USERNAME') or \
                         os.environ.get('HF_USER') or \
                         os.environ.get('SPACE_AUTHOR_NAME')

            if not hf_profile and space_id != 'unknown' and '/' in space_id:
                hf_profile = space_id.split('/')[0]

            # Basic env info
            env_vars = {}
            for k, v in os.environ.items():
                if any(secret_key in k.upper() for secret_key in ["TOKEN", "KEY", "SECRET", "PASS", "AUTH"]):
                    env_vars[k] = "***"
                else:
                    env_vars[k] = v

            info = {
                "space_id": space_id,
                "hf_profile_detected": hf_profile,
                "env_vars": env_vars,
                "hf_connection": "Unknown",
                "whoami": None
            }

            if token:
                try:
                    # Use whoami to verify token and get user/org info
                    whoami_url = "https://huggingface.co/api/whoami-v2"
                    headers = {"Authorization": f"Bearer {token}"}
                    whoami_res = requests.get(whoami_url, headers=headers, timeout=5)
                    if whoami_res.status_code == 200:
                        whoami_data = whoami_res.json()
                        info["whoami"] = {
                            "name": whoami_data.get("name"),
                            "fullname": whoami_data.get("fullname"),
                            "email": whoami_data.get("email"),
                            "orgs": [org.get("name") for org in whoami_data.get("orgs", [])]
                        }
                        info["hf_connection"] = "Authenticated"
                    else:
                        info["hf_connection"] = f"Authentication Failed ({whoami_res.status_code})"

                    # Check space metadata if space_id is known
                    if space_id != 'unknown':
                        url = f"https://huggingface.co/api/spaces/{space_id}"
                        response = requests.get(url, headers=headers, timeout=5)
                        if response.status_code == 200:
                            info["hf_connection"] = "Authenticated & Space Found"
                            info["space_metadata"] = response.json()

                            # Try to get logs too
                            build_logs_url = f"https://huggingface.co/api/spaces/{space_id}/logs/build"
                            run_logs_url = f"https://huggingface.co/api/spaces/{space_id}/logs/run"

                            info["build_logs"] = requests.get(build_logs_url, headers=headers, timeout=5).text[:5000]
                            info["run_logs"] = requests.get(run_logs_url, headers=headers, timeout=5).text[:5000]
                        else:
                            info["hf_space_status"] = f"Space not found or inaccessible ({response.status_code})"

                except Exception as e:
                    info["hf_connection"] = f"Error: {str(e)}"
            else:
                info["hf_connection"] = "No Token Found"

            self.send_response(200, 'OK')
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(info).encode('utf-8'))

        def handle_repo_add_api(self):
            import json
            import os
            from .gitautodeploy import GitAutoDeploy

            content_length = int(self.headers.get('content-length', 0))
            if content_length == 0:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": "Empty request"}).encode('utf-8'))
                return

            request_body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(request_body)
                repo_url = data.get('url')
                inject_actions = data.get('inject_actions', False)
                if not repo_url:
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "message": "Missing URL"}).encode('utf-8'))
                    return

                # Auto-generate Space ID from URL
                # e.g. https://github.com/user/repo -> user/repo
                import re
                match = re.search(r'github\.com[:/]([^/]+/[^/.]+)(\.git)?', repo_url)
                if not match:
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "message": "Invalid GitHub URL"}).encode('utf-8'))
                    return

                repo_name = match.group(1)
                # Robust profile detection: try various common env vars used in HF Spaces
                hf_profile = os.environ.get('HF_PROFILE') or \
                             os.environ.get('HF_Profile') or \
                             os.environ.get('HF_USERNAME') or \
                             os.environ.get('HF_USER') or \
                             os.environ.get('SPACE_AUTHOR_NAME')

                if not hf_profile:
                    # Fallback to extracting from SPACE_ID if available (e.g. "user/space" -> "user")
                    space_id_env = os.environ.get('SPACE_ID')
                    if space_id_env and '/' in space_id_env:
                        hf_profile = space_id_env.split('/')[0]

                if not hf_profile:
                    # Fallback to current authenticated user from whoami
                    token = os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN') or os.environ.get('hf_token')
                    if token:
                        try:
                            whoami_url = "https://huggingface.co/api/whoami-v2"
                            headers = {"Authorization": f"Bearer {token}"}
                            whoami_res = requests.get(whoami_url, headers=headers, timeout=5)
                            if whoami_res.status_code == 200:
                                hf_profile = whoami_res.json().get('name')
                        except:
                            pass

                if not hf_profile:
                    # Final fallback if everything fails
                    hf_profile = 'user'

                space_id = hf_profile + '/' + repo_name.split('/')[-1]

                # Determine which token env var to use for the command string
                hf_token_var = 'HF_TOKEN' if 'HF_TOKEN' in os.environ else 'HUGGING_FACE_HUB_TOKEN'

                # Use absolute path for scripts/deploy_to_hf.py to avoid relative path issues
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                deploy_script = os.path.join(base_dir, 'scripts', 'deploy_to_hf.py')

                repo_config = {
                    'url': repo_url,
                    'branch': 'main',
                    'remote': 'origin',
                    'path': f'/app/repositories/{repo_name.split("/")[-1]}',
                    'deploy': f'python3 {deploy_script} --repo-path . --space-id {space_id} --branch %branch% --create --token ${hf_token_var}',
                    'huggingface_space': space_id,
                    'report_to_jules': True
                }

                success, msg = GitAutoDeploy().add_repository(repo_config, inject_actions=inject_actions)

                response_data = {"success": success, "message": msg, "repo_config": repo_config}
                self.send_response(200, 'OK')
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(response_data).encode('utf-8'))

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": str(e)}).encode('utf-8'))

        def do_POST(self):
            """Invoked on incoming POST requests"""

            if self.path == "/api/repo/add":
                self.handle_repo_add_api()
                return

            from threading import Timer
            import logging
            import json
            import threading
            try:
                from urlparse import parse_qs
            except (ModuleNotFoundError, ImportError):
                from urllib.parse import parse_qs

            logger = logging.getLogger()

            content_length = int(self.headers.get('content-length', 0))
            request_body = self.rfile.read(content_length).decode('utf-8')

            # Extract request headers and make all keys to lowercase (makes them easier to compare)
            request_headers = dict(self.headers)
            request_headers = dict((k.lower(), v) for k, v in request_headers.items())

            action = WebhookAction(self.client_address, request_headers, request_body)
            self._event_store.register_action(action)
            action.set_waiting(True)

            action.log_info('Incoming request from %s:%s' % (self.client_address[0], self.client_address[1]))

            # Payloads from GitHub can be delivered as form data. Test the request for this pattern and extract json payload
            if 'content-type' in request_headers and request_headers['content-type'] == 'application/x-www-form-urlencoded':
                res = parse_qs(request_body)
                if 'payload' in res and len(res['payload']) == 1:
                    request_body = res['payload'][0]

            # Test case debug data
            test_case = {
                'headers': dict(self.headers),
                'payload': json.loads(request_body),
                'config': {},
                'expected': {'status': 200, 'data': [{'deploy': 0}]}
            }

            try:

                # Will raise a ValueError exception if it fails
                ServiceRequestHandler = get_service_handler(request_headers, request_body, action)

                # Unable to identify the source of the request
                if not ServiceRequestHandler:
                    self.send_error(400, 'Unrecognized service')
                    test_case['expected']['status'] = 400
                    action.log_error("Unable to find appropriate handler for request. The source service is not supported")
                    action.set_waiting(False)
                    action.set_success(False)
                    return

                service_handler = ServiceRequestHandler(self._config)

                action.log_info("Handling the request with %s" % ServiceRequestHandler.__name__)

                # Could be GitHubParser, GitLabParser or other
                projects = service_handler.get_matching_projects(request_headers, request_body, action)

                action.log_info("%s candidates matches the request" % len(projects))

                # request_filter = WebhookRequestFilter()

                if len(projects) == 0:
                    self.send_error(400, 'Bad request')
                    test_case['expected']['status'] = 400
                    action.log_error("No matching projects")
                    action.set_waiting(False)
                    action.set_success(False)
                    return

                # Apply filters
                matching_projects = []
                for project in projects:
                    if project.apply_filters(request_headers, request_body, action):
                        matching_projects.append(project)

                # Only keep projects that matches
                projects = matching_projects

                action.log_info("%s candidates matches after applying filters" % len(projects))

                if not service_handler.validate_request(request_headers, request_body, projects, action):
                    self.send_error(400, 'Bad request')
                    test_case['expected']['status'] = 400
                    action.log_warning("Request was rejected due to a secret token mismatch")
                    action.set_waiting(False)
                    action.set_success(False)
                    return

                test_case['expected']['status'] = 200

                self.send_response(200, 'OK')
                self.send_header('Content-type', 'text/plain')
                self.end_headers()

                if len(projects) == 0:
                    action.set_waiting(False)
                    action.set_success(False)
                    return

                action.log_info("Proceeding with %s candidates" % len(projects))
                action.set_waiting(False)
                action.set_success(True)

                for project in projects:

                    # Schedule the execution of the webhook (git pull and trigger deploy etc)
                    thread = threading.Thread(target=project.execute_webhook, args=[self._event_store])
                    thread.start()

                    # Add additional test case data
                    test_case['config'] = {
                        'url': 'url' in project and project['url'],
                        'branch': 'branch' in project and project['branch'],
                        'remote': 'remote' in project and project['remote'],
                        'deploy': 'echo test!'
                    }

            except ValueError as e:
                self.send_error(400, 'Unprocessable request')
                action.log_warning('Unable to process incoming request from %s:%s' % (self.client_address[0], self.client_address[1]))
                test_case['expected']['status'] = 400
                action.set_waiting(False)
                action.set_success(False)
                return

            except Exception as e:
                self.send_error(500, 'Unable to process request')
                test_case['expected']['status'] = 500
                action.log_warning("Unable to process request")
                action.set_waiting(False)
                action.set_success(False)

                raise e

            finally:

                # Save the request as a test case
                if 'log-test-case' in self._config and self._config['log-test-case']:
                    self.save_test_case(test_case)

        def log_message(self, format, *args):
            """Overloads the default message logging method to allow messages to
            go through our custom logger instead."""
            import logging
            logger = logging.getLogger()
            logger.info("%s - %s" % (self.client_address[0], format%args))

        def save_test_case(self, test_case):
            """Log request information in a way it can be used as a test case."""
            import time
            import json
            import os

            # Mask some header values
            masked_headers = ['x-github-delivery', 'x-hub-signature']
            for key in test_case['headers']:
                if key in masked_headers:
                    test_case['headers'][key] = 'xxx'

            target = '%s-%s.tc.json' % (self.client_address[0], time.strftime("%Y%m%d%H%M%S"))
            if 'log-test-case-dir' in self._config and self._config['log-test-case-dir']:
                target = os.path.join(self._config['log-test-case-dir'], target)

            file = open(target, 'w')
            file.write(json.dumps(test_case, sort_keys=True, indent=4))
            file.close()

        def get_server_status(self):
            """Generate a copy of the server status object that contains the public IP or hostname."""

            server_status = {}
            for item in self._server_status.items():
                key, value = item
                public_host = self.headers.get('host').split(':')[0]

                if key == 'http-uri':
                    server_status[key] = value.replace(self._config['http-host'], public_host)

                if key == 'https-uri':
                    server_status[key] = value.replace(self._config['https-host'], public_host)

                if key == 'wss-uri':
                    server_status[key] = value.replace(self._config['wss-host'], public_host)

            return server_status

        def validate_web_ui_enabled(self):
            """Verify that the Web UI is enabled"""

            if self._config['web-ui-enabled']:
                return True

            self.send_error(403, "Web UI is not enabled")
            return False

        def validate_web_ui_https(self):
            """Verify that the request is made over HTTPS"""

            if self._is_https:
                return True

            if not self._config['web-ui-require-https']:
                return True

            # Attempt to redirect the request to HTTPS
            server_status = self.get_server_status()
            if 'https-uri' in server_status:
                self.send_response(307)
                self.send_header('Location', '%s%s' % (server_status['https-uri'], self.path))
                self.end_headers()
                return False

            self.send_error(403, "Web UI is only accessible through HTTPS")
            return False

        def validate_web_ui_whitelist(self):
            """Verify that the client address is whitelisted"""

            # Allow all if whitelist is empty
            if len(self._config['web-ui-whitelist']) == 0:
                return True

            # Verify that client IP is whitelisted
            if self.client_address[0] in self._config['web-ui-whitelist']:
                return True

            self.send_error(403, "%s is not allowed access" % self.client_address[0])
            return False

        def validate_web_ui_basic_auth(self):
            """Authenticate the user"""
            import base64

            if not self._config['web-ui-auth-enabled']:
                return True

            # Verify that a username and password is specified in the config
            if self._config['web-ui-username'] is None or self._config['web-ui-password'] is None:
                self.send_error(403, "Authentication credentials missing in config")
                return False

            # Verify that the provided username and password matches the ones in the config
            key = base64.b64encode("%s:%s" % (self._config['web-ui-username'], self._config['web-ui-password']))
            if self.headers.getheader('Authorization') == 'Basic ' + key:
                return True

            # Let the client know that authentication is required
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm=\"GAD\"')
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write('Not authenticated')
            return False

    return WebhookRequestHandler
