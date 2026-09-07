"""Browser QA server: real frontend + read-only catalog + synthetic collections.

Run from project root: .venv/bin/python -m tests.frontend.serve_fixture
All write requests are rejected. No user collections, files, or model APIs are used.
"""
from http import HTTPStatus
from http.server import ThreadingHTTPServer

from backend.api.server import Handler


def collection(cid):
    return {
        'collection_id': cid, 'name': f'QA 集合 {cid}', 'description': '隔离验证，不含真实论文',
        'member_count': int(cid == 'A'), 'source_type': 'manual',
        'updated_at': '2026-09-03T00:00:00+00:00',
    }


class FixtureHandler(Handler):
    def handle_api(self, path, params):
        if path == '/api/collections':
            self.send_json({'items': [collection('A'), collection('B')]})
        elif path in ('/api/collections/A', '/api/collections/B'):
            cid = path.rsplit('/', 1)[-1]
            items = [] if cid == 'B' else [{'entity_id': 'qa-paper-a', 'title': 'QA Paper A',
                                          'status': 'current', 'appearances': []}]
            self.send_json({'collection': collection(cid), 'items': items,
                            'counts': {'current': len(items), 'alias': 0, 'missing': 0}})
        elif path == '/api/download-plan':
            cid = params.get('collection_id', [''])[0]
            items = [] if cid != 'A' else [{'entity_id': 'qa-paper-a', 'title': 'QA Paper A', 'source': 'open'}]
            self.send_json({'summary': {'total': len(items), 'downloadable': len(items), 'counts': {}}, 'items': items})
        elif path == '/api/chats':
            self.send_json({'items': [{'conversation_id': 'chat-qa', 'title': '入库 QA', 'status': 'active',
                                       'created_at': '2026-09-04T00:00:00+00:00', 'updated_at': '2026-09-04T00:00:00+00:00'}]})
        elif path == '/api/chats/chat-qa/messages':
            self.send_json({'conversation': {'conversation_id': 'chat-qa', 'title': '入库 QA', 'status': 'active'}, 'messages': [{'message_id': 'msg-qa', 'conversation_id': 'chat-qa', 'role': 'assistant',
                                          'content': '<｜｜DSML｜｜tool_calls>fixture-only</｜｜DSML｜｜tool_calls>', 'chunks': [], 'tool_trace': [{
                                              'tool': 'ingest_papers', 'ok': True,
                                              'args': {'entity_ids': ['qa-current', 'qa-ready', 'qa-none']},
                                          }], 'created_at': '2026-09-04T00:00:00+00:00'}]})
        elif path == '/api/chats/chat-qa/ingest-tasks':
            self.send_json({'items': []})
        elif path == '/api/ingest-plan':
            self.send_json({'summary': {'total': 3}, 'items': [
                {'entity_id': 'qa-current', 'plan_status': 'already_indexed'},
                {'entity_id': 'qa-ready', 'plan_status': 'local_ready'},
                {'entity_id': 'qa-none', 'plan_status': 'needs_external_resolution'},
            ]})
        elif path == '/api/local-library':
            self.send_json({'summary': {'valid_pdfs': 1, 'parsed': 1, 'indexed_current': 1, 'index_stale': 0, 'chunks': 12, 'qdrant_available': True, 'last_ingest_at': '2026-09-04T00:00:00+00:00'}, 'items': [{'entity_id': 'qa-current', 'title': 'QA Paper A', 'pdf_status': 'success', 'parse_status': 'success', 'index_status': 'indexed_current', 'chunk_count': 12, 'actual_chunk_count': 12, 'pdf_source': 'fixture', 'failure': ''}]})
        elif path in ('/api/summary', '/api/venues', '/api/years', '/api/papers', '/api/topics', '/api/venue-overview'):
            super().handle_api(path, params)
        else:
            self.send_error_json(HTTPStatus.NOT_FOUND, 'QA fixture endpoint not provided')

    def do_POST(self):
        self.send_error_json(HTTPStatus.FORBIDDEN, 'QA fixture: writes disabled')

    do_DELETE = do_POST
    do_PATCH = do_POST
    do_PUT = do_POST


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', 8766), FixtureHandler)
    print('Read-only browser fixture: http://127.0.0.1:8766', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
