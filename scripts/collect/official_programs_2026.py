"""Parse public 2026 conference programs without inventing absent bibliographic fields."""
from dataclasses import asdict
from urllib.parse import urlparse, parse_qs
from lxml import html
from scripts.collect.update_catalog import base, normalize, set_abstract, title_key

EMNLP_SOURCE = 'https://2026.emnlp.org/program/'
EMNLP_SHEET = 'https://docs.google.com/spreadsheets/d/1aXGTy_7Xeh-OXIs3iJSbDpUsZ6YhbfA76b17kBZH0Yk/edit?gid=943432490'
MM_SOURCE = 'https://2026.acmmm.org/site/technical-programme.html'
MM_DATA = 'https://2026.acmmm.org/site/program-data.json'


def pmlr_identity(content, expected_title):
    """Use PMLR's explicit OpenReview link to resolve renamed ICML papers."""
    tree = html.fromstring(content.decode('utf-8'))
    titles = tree.xpath('//meta[@name="citation_title"]/@content')
    if len(titles) != 1 or title_key(titles[0]) != title_key(expected_title):
        # A literal quotation mark can break citation_title's HTML attribute.
        # A unique visible article heading must still exactly match the volume.
        headings = tree.xpath('//h1')
        if len(headings) != 1 or title_key(headings[0].text_content()) != title_key(expected_title):
            raise ValueError('PMLR title does not match volume entry')
    authors = tree.xpath('//meta[@name="citation_author"]/@content')
    ids = set()
    for link in tree.xpath('//a[@href]/@href'):
        parsed = urlparse(link)
        if parsed.hostname == 'openreview.net' and parsed.path == '/forum':
            ids.update(parse_qs(parsed.query).get('id', []))
    ids.discard('')
    abstracts = tree.xpath('//*[@id="abstract"]')
    if not authors or len(ids) != 1 or len(abstracts) != 1:
        raise ValueError('Missing or ambiguous PMLR authors, shared identity or abstract')
    return {'openreview_id': next(iter(ids)), 'authors': authors,
            'abstract': abstracts[0].text_content().strip()}


def _unique(rows):
    found = {}
    for row in rows:
        key = row['paper_id']
        if not row['title'] or not key:
            raise ValueError('Missing paper title or identity')
        old = found.get(key)
        if old:
            if title_key(old['title']) != title_key(row['title']) or old['authors'] != row['authors']:
                raise ValueError('Conflicting repeated program identity: ' + key)
            if old['abstract'] and row['abstract'] and old['abstract'] != row['abstract']:
                raise ValueError('Conflicting repeated abstract: ' + key)
            if not old['abstract'] and row['abstract']:
                found[key] = row
        else:
            found[key] = row
    return list(found.values())


def emnlp_main(items):
    rows = []
    for item in items:
        pid = item['Paper ID #-track)'].strip()
        if not pid.endswith('-MAIN'):
            continue
        # Presenter names are not author lists. Leave authors explicitly unavailable.
        row = asdict(base.Paper(paper_id='program-' + pid, venue='EMNLP',
            venue_type='conference', year=2026, track='main', title=item['Title'], authors='',
            paper_url='', source_name='EMNLP official presenter schedule (author list unavailable)',
            source_url=EMNLP_SHEET, source_tier='official', verification_status='rolling', list_status='rolling'))
        rows.append(normalize(row))
    return _unique(rows)


def acmmm_main(payload):
    rows = []
    for entry in payload['entries']:
        for item in entry.get('papers', []):
            if item.get('track') != 'Main Track':
                continue
            title = item['title'].replace('\\u200b', '').replace('\u200b', '').strip()
            row = asdict(base.Paper(paper_id='program-' + str(item['paper_id']), venue='ACM MM',
                venue_type='conference', year=2026, track='main', title=title,
                authors='; '.join(item['authors']), paper_url='', source_name='ACM Multimedia official technical programme',
                source_url=MM_DATA, source_tier='official', verification_status='rolling', list_status='rolling'))
            # Deliberately whitelist fields: do not copy emails or affiliations from program JSON.
            set_abstract(row, item.get('abstract', ''), MM_DATA, row['source_name'])
            rows.append(normalize(row))
    return _unique(rows)
