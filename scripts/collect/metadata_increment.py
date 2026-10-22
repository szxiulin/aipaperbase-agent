"""Conservative metadata gates shared by incremental collection and its tests."""
from datetime import date
from scripts.collect.update_catalog import title_key, set_abstract
from scripts.collect.emnlp_openalex import presenter_matches


def registry_matches(candidate, metadata, expected_journal, cutoff):
    """Keep source date precision; a year alone never proves an exact date."""
    parts = (metadata.get('published') or {}).get('date-parts', [[]])[0]
    authors = metadata.get('author') or []
    if not parts or parts[0] != candidate['year'] or not authors:
        return False
    if tuple(parts) > (cutoff.year, cutoff.month, cutoff.day)[:len(parts)]:
        return False
    return (metadata.get('type') == 'journal-article'
            and metadata.get('DOI', '').casefold() == candidate['doi'].casefold()
            and any(title_key(t) == title_key(candidate['title']) for t in metadata.get('title', []))
            and expected_journal in (metadata.get('container-title') or [])
            and all(a.get('family') or a.get('name') for a in authors))


def fill_missing(row, evidence, presenter, source_name):
    """Preserve identifiers and existing metadata; a presenter alone is not a byline."""
    authors = evidence.get('authors') or []
    if (title_key(row['title']) != title_key(evidence['title'])
            or not all(isinstance(a, str) and a.strip() for a in authors)
            or not presenter_matches(presenter, authors)):
        raise ValueError('Title or presenter does not verify the author list')
    result = dict(row)
    if not result['authors'].strip():
        result['authors'] = '; '.join(a.strip() for a in authors)
    set_abstract(result, evidence.get('abstract', ''), evidence['source_url'], source_name,
                 'official_plus_secondary')
    return result


def verify_visible_authors(registered, visible):
    """A collapsed official header can verify a prefix, never delete later authors."""
    full = [a.strip() for a in registered.split(';') if a.strip()]
    if not visible or full[:len(visible)] != visible:
        raise ValueError('Visible byline conflicts with registered author order')
    return 'header_prefix_only' if len(full) > len(visible) else 'header_matches_registered_list'
