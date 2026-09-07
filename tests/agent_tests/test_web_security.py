"""Offline SSRF regressions: fake DNS and Playwright hooks, never a real browser."""

import socket
import unittest
import urllib.parse
from types import SimpleNamespace
from unittest import mock

from backend.agent.tools import web


def dns_answers(*addresses):
    return [
        (socket.AF_INET6 if ":" in address else socket.AF_INET,
         socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))
        for address in addresses
    ]


class URLSecurityTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("socket.getaddrinfo", return_value=dns_answers("93.184.216.34"))
        self.dns = patcher.start()
        self.addCleanup(patcher.stop)

    def test_public_domain_baseline(self):
        for url in ("https://example.com/article?q=test#main", "HTTP://EXAMPLE.COM:80/",
                    "https://example.com./", "https://xn--bcher-kva.de/",
                    "https://news127.example.com/"):
            with self.subTest(url=url):
                self.assertTrue(web._valid_http_url(url))
        self.assertTrue(self.dns.called)

    def test_malformed_urls_are_rejected_without_raising(self):
        for url in (None, 123, "", "https://[broken", "https://example.com:bad/",
                    "https://example.com:65536/", "https://example.com:0/",
                    "https://example.com:/", "https://user:pass@example.com/",
                    "https://example.com\\@public.test/", "https://exam\nple.com/",
                    " https://example.com/", "https://example.com/\x00",
                    "https://example.com/\x7f", "https://exa mple.com/",
                    "https://%31%32%37.0.0.1/", "https://example..com/",
                    "https://-example.com/", "https://example.com../", "https://faß.de/",
                    "https://example.com/" + "x" * 2048):
            with self.subTest(url=url):
                self.assertFalse(web._valid_http_url(url))

    def test_non_http_and_local_names(self):
        for url in ("file:///etc/passwd", "ftp://example.com/", "ws://example.com/",
                    "//example.com/", "http://localhost/", "http://api.localhost/",
                    "http://intranet/"):
            with self.subTest(url=url):
                self.assertFalse(web._valid_http_url(url))

    def test_ip_literals_and_browser_numeric_variants(self):
        # Preserve the existing domain-only policy, even for public IP literals.
        for host in ("127.0.0.1", "127.0.0.1.", "127.1", "2130706433",
                     "0177.0.0.1", "0x7f.0.0.1", "0x7f000001", "0x7f.1",
                     "1.2.65535", "8.8.8.8", "8.8.8.8.", "0x08080808",
                     "[::1]", "[::ffff:127.0.0.1]", "[2001:4860:4860::8888]",
                     "[fe80::1%25en0]", "１２７.０.０.１", "１２７。１"):
            with self.subTest(host=host):
                self.assertFalse(web._valid_http_url("http://" + host + "/"))
        self.dns.assert_not_called()

    def test_non_public_dns_answers(self):
        for address in ("127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1",
                        "169.254.169.254", "100.64.0.1", "0.0.0.0", "192.0.2.1",
                        "224.0.0.1", "240.0.0.1", "::1", "::", "fc00::1",
                        "fe80::1", "ff02::1", "2001:db8::1", "::ffff:127.0.0.1"):
            with self.subTest(address=address):
                self.dns.return_value = dns_answers(address)
                self.assertFalse(web._valid_http_url("https://example.com/"))

    def test_every_dns_answer_must_be_public(self):
        for addresses in (("93.184.216.34", "10.0.0.1"),
                          ("::1", "93.184.216.34"),
                          ("93.184.216.34", "fc00::1")):
            with self.subTest(addresses=addresses):
                self.dns.return_value = dns_answers(*addresses)
                self.assertFalse(web._valid_http_url("https://example.com/"))

    def test_public_ipv4_and_ipv6_dns(self):
        self.dns.return_value = dns_answers("93.184.216.34", "2606:4700:4700::1111")
        self.assertTrue(web._valid_http_url("https://example.com/"))

    def test_dns_failure_empty_or_unparseable_is_denied(self):
        self.dns.side_effect = socket.gaierror("offline")
        self.assertFalse(web._valid_http_url("https://example.com/"))
        self.dns.side_effect = None
        for answers in ([], dns_answers("not-an-ip")):
            with self.subTest(answers=answers):
                self.dns.return_value = answers
                self.assertFalse(web._valid_http_url("https://example.com/"))

    def test_rejection_does_not_launch_or_spend_budget(self):
        self.dns.return_value = dns_answers("10.0.0.1")
        ctx = {}
        with mock.patch.object(web, "_browser_page") as browser_page:
            result = web._browser_fetch_page(ctx, "https://example.com/")
        self.assertFalse(result.ok)
        self.assertNotIn("web_budget", ctx)
        browser_page.assert_not_called()

    def test_public_fetch_contract_is_preserved(self):
        ctx = {}
        with mock.patch.object(web, "_browser_page", return_value={
            "title": "Example", "text": "Public content", "links": [{"href": "https://example.com/"}]
        }):
            result = web._browser_fetch_page(ctx, "https://example.com/", extract="all")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["text"], "Public content")
        self.assertEqual(len(result.data["links"]), 1)
        self.assertEqual(result.provenance[0]["url"], "https://example.com/")
        self.assertEqual(ctx["web_budget"]["used"], 1)


class BrowserSecurityTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("socket.getaddrinfo", return_value=dns_answers("93.184.216.34"))
        self.dns = patcher.start()
        self.addCleanup(patcher.stop)
        self.page = mock.Mock(spec=["goto", "title", "evaluate", "eval_on_selector_all", "wait_for_load_state",
                                    "url", "main_frame"])
        self.page.url = "https://example.com/"
        self.page.main_frame = object()
        self.page.title.return_value = " Example "
        self.page.evaluate.return_value = "x" * (web.MAX_TEXT + 1)
        self.page.eval_on_selector_all.return_value = []
        self.context = mock.Mock(spec=["new_page", "route", "route_web_socket"])
        self.context.new_page.return_value = self.page
        self.browser = mock.Mock(spec=["new_context", "new_page", "close"])
        self.browser.new_context.return_value = self.context
        self.browser.new_page.return_value = self.page  # Old implementation, for red phase.
        manager = mock.MagicMock()
        manager.__enter__.return_value.chromium.launch.return_value = self.browser
        self.playwright = mock.Mock(return_value=manager)
        # Playwright is an optional runtime dependency; these tests need no installation.
        patcher = mock.patch.dict("sys.modules", {
            "playwright": SimpleNamespace(),
            "playwright.sync_api": SimpleNamespace(sync_playwright=self.playwright),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def installed_handler(self):
        self.context.route.assert_called_once()
        pattern, handler = self.context.route.call_args.args
        self.assertEqual(pattern, "**/*")
        return handler

    def request(self, url, status=200, resource_type="document"):
        route = mock.Mock(spec=["request", "fetch", "fulfill", "abort", "continue_"])
        route.request = SimpleNamespace(url=url, resource_type=resource_type, method="GET",
                                        post_data_buffer=None, headers={"user-agent": web.UA},
                                        frame=self.page.main_frame,
                                        is_navigation_request=lambda: resource_type == "document")
        response = mock.Mock(spec=["status", "headers", "dispose"])
        response.status = status
        response.headers = {"location": "http://127.0.0.1/"} if 300 <= status < 400 else {}
        route.fetch.return_value = response
        return route

    def test_context_hooks_installed_before_page_creation(self):
        def new_page():
            self.installed_handler()
            self.context.route_web_socket.assert_called_once()
            return self.page

        self.context.new_page.side_effect = new_page
        result = web._browser_page("https://example.com/")
        self.browser.new_context.assert_called_once_with(user_agent=web.UA, service_workers="block")
        self.browser.new_page.assert_not_called()
        self.assertEqual(result["title"], "Example")
        self.assertEqual(len(result["text"]), web.MAX_TEXT)
        self.browser.close.assert_called_once()

    def test_direct_entry_rejects_private_dns_before_browser_start(self):
        self.dns.return_value = dns_answers("10.0.0.1")
        with self.assertRaises(ValueError):
            web._browser_page("https://example.com/")
        self.playwright.assert_not_called()

    def test_guard_returns_final_url(self):
        route = self.request("https://example.com/start", status=302)
        redirect = route.fetch.return_value
        redirect.headers = {"location": "https://cdn.example.com/articles/index.html"}
        final = self.request("https://cdn.example.com/articles/index.html").fetch.return_value
        route.fetch.side_effect = [redirect, final]
        self.assertEqual(web._guard_browser_request(route), "https://cdn.example.com/articles/index.html")

    def test_main_document_final_url_is_opened_before_extraction(self):
        start, final_url = "https://example.com/start", "https://cdn.example.com/articles/index.html"
        routes = []

        def navigate(url, **kwargs):
            self.page.url = url  # fulfill alone does not change document URL/base URI.
            route = self.request(url)
            if url == start:
                redirect = self.request(url, status=302).fetch.return_value
                redirect.headers = {"location": final_url}
                route.fetch.side_effect = [redirect, route.fetch.return_value]
            routes.append(route)
            self.installed_handler()(route)

        def extract_links(*args):
            self.assertEqual(self.page.url, final_url)
            self.assertEqual(self.page.goto.call_count, 2)
            # Simulate native href resolution from the actual document address,
            # not the response's fetch URL. No real DOM/browser is used here.
            return [{"text": "Paper", "href": urllib.parse.urljoin(self.page.url, "paper.pdf")}]

        def extract_text(*args):
            self.assertEqual(self.page.url, final_url)
            self.assertEqual(self.page.goto.call_count, 2)
            return "Final document text"

        self.page.goto.side_effect = navigate
        self.page.evaluate.side_effect = extract_text
        self.page.eval_on_selector_all.side_effect = extract_links
        result = web._browser_page(start)
        self.assertEqual([call.args[0] for call in self.page.goto.call_args_list], [start, final_url])
        self.assertEqual(result["links"][0]["href"], "https://cdn.example.com/articles/paper.pdf")
        self.assertTrue(all(call.kwargs["max_redirects"] == 0
                            for route in routes for call in route.fetch.call_args_list))

    def test_subresource_and_child_frame_do_not_change_main_document_target(self):
        def navigate(url, **kwargs):
            self.page.url = url
            handler = self.installed_handler()
            handler(self.request(url))
            for resource_type in ("script", "document"):
                route = self.request("https://example.com/child", status=302, resource_type=resource_type)
                if resource_type == "document":
                    route.request.frame = object()  # iframe or another page, not this main frame.
                redirect = route.fetch.return_value
                redirect.headers = {"location": "https://cdn.example.com/child"}
                final = self.request("https://cdn.example.com/child").fetch.return_value
                route.fetch.side_effect = [redirect, final]
                handler(route)
                route.fulfill.assert_called_once_with(response=final)

        self.page.goto.side_effect = navigate
        web._browser_page("https://example.com/")
        self.page.goto.assert_called_once()
        self.assertEqual(self.page.url, "https://example.com/")

    def test_main_document_renavigation_is_bounded_before_extraction(self):
        def navigate(url, **kwargs):
            self.page.url = url
            route = self.request(url, status=302)
            redirect = route.fetch.return_value
            redirect.headers = {"location": f"https://example.com/change-{self.page.goto.call_count}"}
            final = self.request("https://example.com/final").fetch.return_value
            route.fetch.side_effect = [redirect, final]
            self.installed_handler()(route)

        self.page.goto.side_effect = navigate
        with self.assertRaisesRegex(ValueError, "navigation limit"):
            web._browser_page("https://example.com/start")
        self.assertEqual(self.page.goto.call_count, web.MAX_REDIRECTS + 1)
        self.page.evaluate.assert_not_called()
        self.page.eval_on_selector_all.assert_not_called()
        self.browser.close.assert_called_once()

    def test_all_resource_types_are_checked_before_fetch(self):
        def navigate(*args, **kwargs):
            handler = self.installed_handler()
            self.dns.return_value = dns_answers("10.0.0.1")
            for resource_type in ("document", "script", "stylesheet", "image", "fetch", "xhr", "other"):
                with self.subTest(resource_type=resource_type):
                    route = self.request("https://internal.example.com/", resource_type=resource_type)
                    handler(route)
                    route.abort.assert_called_once()
                    route.fetch.assert_not_called()
                    route.continue_.assert_not_called()

        self.page.goto.side_effect = navigate
        web._browser_page("https://example.com/")

    def test_public_request_is_fetched_without_automatic_redirects(self):
        web._browser_page("https://example.com/")
        route = self.request("https://cdn.example.com/app.js", resource_type="script")
        self.installed_handler()(route)
        route.fetch.assert_called_once_with(max_redirects=0, timeout=web.BROWSER_TIMEOUT_MS)
        route.fulfill.assert_called_once_with(response=route.fetch.return_value)
        route.continue_.assert_not_called()
        route.abort.assert_not_called()
        route.fetch.return_value.dispose.assert_called_once()

    def test_public_and_relative_redirects_succeed_without_browser_redirect(self):
        web._browser_page("https://example.com/")
        for target, expected in (("https://other-public.example.com/article", "https://other-public.example.com/article"),
                                 ("../article", "https://example.com/article"),
                                 ("//cdn.example.com/article", "https://cdn.example.com/article")):
            with self.subTest(target=target):
                route = self.request("https://example.com/start/path", status=302)
                redirect = route.fetch.return_value
                redirect.headers = {"location": target}
                final = self.request(expected).fetch.return_value
                route.fetch.side_effect = [redirect, final]
                self.installed_handler()(route)
                self.assertEqual(route.fetch.call_count, 2)
                self.assertEqual(route.fetch.call_args.kwargs["url"], expected)
                self.assertTrue(all(call.kwargs["max_redirects"] == 0 for call in route.fetch.call_args_list))
                route.fulfill.assert_called_once_with(response=final)
                route.abort.assert_not_called()
                route.continue_.assert_not_called()
                redirect.dispose.assert_called_once()
                final.dispose.assert_called_once()

    def test_redirect_targets_with_private_ip_or_dns_are_never_fetched(self):
        web._browser_page("https://example.com/")
        handler = self.installed_handler()
        self.dns.side_effect = lambda host, *args, **kwargs: dns_answers(
            "10.0.0.1" if host == "internal.example.com" else "93.184.216.34")
        for status in (301, 302, 303, 307, 308):
            for target in ("http://127.0.0.1/", "https://internal.example.com/",
                           "http://2130706433/", "file:///etc/passwd", "\nhttps://example.com/"):
                with self.subTest(status=status, target=target):
                    route = self.request("https://example.com/redirect", status=status)
                    route.fetch.return_value.headers = {"location": target}
                    handler(route)
                    route.fetch.assert_called_once_with(max_redirects=0, timeout=web.BROWSER_TIMEOUT_MS)
                    route.abort.assert_called_once()
                    route.fulfill.assert_not_called()
                    route.continue_.assert_not_called()
                    route.fetch.return_value.dispose.assert_called_once()

    def test_multi_hop_dns_validation_and_redirect_limit(self):
        web._browser_page("https://example.com/")
        handler = self.installed_handler()
        self.dns.side_effect = lambda host, *args, **kwargs: dns_answers(
            "10.0.0.1" if host == "internal.example.com" else "93.184.216.34")
        route = self.request("https://example.com/", status=302)
        first = route.fetch.return_value
        first.headers = {"location": "https://public.example.com/"}
        second = self.request("https://public.example.com/", status=307).fetch.return_value
        second.headers = {"location": "https://internal.example.com/"}
        route.fetch.side_effect = [first, second]
        handler(route)
        self.assertEqual(route.fetch.call_count, 2)
        route.abort.assert_called_once()
        route.fulfill.assert_not_called()
        for target in ("/loop", "/other"):
            with self.subTest(target=target):
                loop = self.request("https://example.com/loop", status=302)
                loop.fetch.return_value.headers = {"location": target}
                handler(loop)
                self.assertEqual(loop.fetch.call_count, web.MAX_REDIRECTS + 1)
                loop.abort.assert_called_once()
                loop.fulfill.assert_not_called()

    def test_redirect_method_body_and_sensitive_header_semantics(self):
        web._browser_page("https://example.com/")
        for status, method, expected_method in ((301, "POST", "GET"), (302, "POST", "GET"),
                                                 (301, "PUT", "PUT"), (302, "GET", "GET"),
                                                 (303, "PUT", "GET"), (303, "HEAD", "HEAD"),
                                                 (307, "POST", "POST"), (308, "PUT", "PUT")):
            with self.subTest(status=status, method=method):
                route = self.request("https://example.com/", status=status)
                route.request.method = method
                route.request.post_data_buffer = b"payload"
                route.request.headers = {"authorization": "secret", "cookie": "session=secret",
                                         "proxy-authorization": "secret", "host": "example.com",
                                         "content-type": "text/plain", "content-length": "7",
                                         "content-encoding": "gzip", "content-language": "en",
                                         "content-location": "/old", "transfer-encoding": "chunked"}
                redirect = route.fetch.return_value
                redirect.headers = {"location": "https://other.example.com/"}
                final = self.request("https://other.example.com/").fetch.return_value
                route.fetch.side_effect = [redirect, final]
                self.installed_handler()(route)
                route.fulfill.assert_called_once_with(response=final)
                options = route.fetch.call_args.kwargs
                self.assertEqual(options["method"], expected_method)
                self.assertEqual(options["post_data"], b"" if expected_method != method else b"payload")
                for header in ("authorization", "cookie", "proxy-authorization", "host"):
                    self.assertNotIn(header, options["headers"])
                if expected_method != method:
                    for header in ("content-type", "content-length", "content-encoding",
                                   "content-language", "content-location", "transfer-encoding"):
                        self.assertNotIn(header, options["headers"])

    def test_relative_location_uses_previous_hop_and_rechecks_dns(self):
        web._browser_page("https://example.com/")
        self.dns.reset_mock()
        route = self.request("https://example.com/start", status=301)
        first = route.fetch.return_value
        first.headers = {"location": "https://cdn.example.com/new/path/"}
        second = self.request("https://cdn.example.com/new/path/", status=302).fetch.return_value
        second.headers = {"location": "../article"}
        final = self.request("https://cdn.example.com/new/article").fetch.return_value
        route.fetch.side_effect = [first, second, final]
        self.installed_handler()(route)
        self.assertEqual([call.args[0] for call in self.dns.call_args_list],
                         ["example.com", "cdn.example.com", "cdn.example.com"])
        self.assertEqual(route.fetch.call_args.kwargs["url"], "https://cdn.example.com/new/article")
        self.assertTrue(all(call.kwargs["max_redirects"] == 0 for call in route.fetch.call_args_list))
        route.fulfill.assert_called_once_with(response=final)
        route.abort.assert_not_called()

    def test_five_redirects_then_final_response_is_allowed(self):
        web._browser_page("https://example.com/")
        route = self.request("https://example.com/start")
        responses = []
        for hop in range(web.MAX_REDIRECTS):
            response = self.request("https://example.com/", status=302).fetch.return_value
            response.headers = {"location": f"/hop-{hop}"}
            responses.append(response)
        final = self.request("https://example.com/final").fetch.return_value
        route.fetch.side_effect = responses + [final]
        self.installed_handler()(route)
        self.assertEqual(route.fetch.call_count, web.MAX_REDIRECTS + 1)
        self.assertTrue(all(call.kwargs["max_redirects"] == 0 for call in route.fetch.call_args_list))
        route.fulfill.assert_called_once_with(response=final)
        route.abort.assert_not_called()
        for response in responses + [final]:
            response.dispose.assert_called_once()

    def test_same_origin_redirect_keeps_authorization(self):
        web._browser_page("https://example.com/")
        route = self.request("https://example.com/start", status=302)
        route.request.headers = {"authorization": "same-origin-token"}
        redirect = route.fetch.return_value
        redirect.headers = {"location": "/next"}
        final = self.request("https://example.com/next").fetch.return_value
        route.fetch.side_effect = [redirect, final]
        self.installed_handler()(route)
        route.fulfill.assert_called_once_with(response=final)
        self.assertEqual(route.fetch.call_args.kwargs["headers"]["authorization"], "same-origin-token")

    def test_redirect_without_location_is_denied(self):
        web._browser_page("https://example.com/")
        route = self.request("https://example.com/", status=302)
        route.fetch.return_value.headers = {}
        self.installed_handler()(route)
        route.abort.assert_called_once()
        route.fulfill.assert_not_called()

    def test_new_navigation_and_normalized_private_targets_are_blocked(self):
        web._browser_page("https://example.com/")
        handler = self.installed_handler()
        for url in ("http://127.0.0.1/", "http://169.254.169.254/", "file:///etc/passwd",
                    "http://[::ffff:127.0.0.1]/", "http://127.1/"):
            with self.subTest(url=url):
                route = self.request(url)
                handler(route)
                route.abort.assert_called_once()
                route.fetch.assert_not_called()
                route.continue_.assert_not_called()

    def test_dns_rechecked_per_request_not_cached(self):
        web._browser_page("https://example.com/")
        handler = self.installed_handler()
        first = self.request("https://example.com/")
        handler(first)
        first.fulfill.assert_called_once()
        self.dns.return_value = dns_answers("127.0.0.1")
        second = self.request("https://example.com/next")
        handler(second)
        second.abort.assert_called_once()
        second.fetch.assert_not_called()
        # This is only a between-request DNS check, NOT a connection-pinning/rebinding proof.

    def test_fetch_failure_aborts_without_continue_fallback(self):
        web._browser_page("https://example.com/")
        route = self.request("https://example.com/")
        route.fetch.side_effect = RuntimeError("fetch unavailable")
        self.installed_handler()(route)
        route.abort.assert_called_once()
        route.fulfill.assert_not_called()
        route.continue_.assert_not_called()

    def test_websockets_are_closed_without_connecting(self):
        web._browser_page("https://example.com/")
        self.context.route_web_socket.assert_called_once()
        pattern, handler = self.context.route_web_socket.call_args.args
        self.assertEqual(pattern, "**/*")
        for url in ("ws://127.0.0.1/", "wss://example.com/socket"):
            with self.subTest(url=url):
                ws = mock.Mock(spec=["url", "close", "connect_to_server"])
                ws.url = url
                handler(ws)
                ws.close.assert_called_once()
                ws.connect_to_server.assert_not_called()

    def test_missing_websocket_routing_fails_closed(self):
        del self.context.route_web_socket
        with self.assertRaises(RuntimeError):
            web._browser_page("https://example.com/")
        self.context.new_page.assert_not_called()
        self.browser.close.assert_called_once()

    def test_browser_closed_on_navigation_failure(self):
        self.page.goto.side_effect = RuntimeError("navigation failed")
        with self.assertRaisesRegex(RuntimeError, "navigation failed"):
            web._browser_page("https://example.com/")
        self.browser.close.assert_called_once()

    def test_search_uses_the_same_guarded_context(self):
        self.assertEqual(web._ddg_search("test query"), [])
        self.installed_handler()
        self.browser.new_context.assert_called_once_with(user_agent=web.UA, service_workers="block")


if __name__ == "__main__":
    unittest.main()
