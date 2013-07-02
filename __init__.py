#!/usr/bin/env python
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai
from __future__ import (unicode_literals, division, absolute_import, print_function)

__license__ = 'GPL v3'
__copyright__ = '2013, Hojel<hojelei@gmail.com>'
__docformat__ = 'restructuredtext ko'

import time
from urllib import quote
from Queue import Queue, Empty

from lxml.html import fromstring, tostring

from calibre import as_unicode
from calibre.ebooks.metadata import check_isbn
from calibre.ebooks.metadata.sources.base import Source
from calibre.utils.cleantext import clean_ascii_chars

class YES24(Source):

    name = 'YES24'
    description = _('Downloads metadata and covers from YES24')
    author = 'Hojel'
    version = (1, 0, 1)
    minimum_calibre_version = (0, 8, 0)

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset(['title', 'authors',
        'identifier:yes24', 'identifier:isbn',
        'publisher', 'pubdate', 'series',
        'comments', 'language'])
    has_html_comments = True
    supports_gzip_transfer_encoding = True

    BASE_URL   = 'http://www.yes24.com'
    BROWSE_URL = 'http://www.yes24.com/24/Goods'
    SEARCH_URL = 'http://www.yes24.com/searchcorner/Search?domain=BOOK'

    def get_book_url(self, identifiers):
        yes24_id = identifiers.get('yes24', None)
        if yes24_id:
            url = '%s/%s' % (YES24.BROWSE_URL, yes24_id)
            return ('yes24', yes24_id, url)

    def identify(self, log, result_queue, abort, title=None, authors=None,
            identifiers={}, timeout=30):
        '''
        Note this method will retry without identifiers automatically if no
        match is found with identifiers.
        '''
        matches = []
        # If we have a YES24 id then we do not need to fire a "search"
        # at yes24.com. Instead we will go straight to the URL for that book.
        yes24_id = identifiers.get('yes24', None)
        br = self.browser
        if yes24_id:
            matches.append('%s/%s' % (YES24.BROWSE_URL, yes24_id))
        else:
            query = self._create_query(log, title=title, authors=authors,
                    identifiers=identifiers)
            if query is None:
                log.error('Insufficient metadata to construct query')
                return

            try:
                log.info('Querying: %s' % query)
                print('Querying ', query)
                response = br.open_novisit(query, timeout=timeout)
                # redirection for ISBN
                #location = response.geturl()
                #matches.append(location)

                try:
                    raw = response.read().strip()
                    #open('P:\\t.html', 'wb').write(raw)
                    raw = raw.decode('euc-kr', errors='replace')
                    if not raw:
                        log.error('Failed to get raw result for query')
                        return
                    root = fromstring(clean_ascii_chars(raw))
                except:
                    msg = 'Failed to parse YES24 page for query'
                    log.exception(msg)
                    return msg

                self._parse_search_results(log, title, authors, root, matches, timeout)
            except Exception as e:
                err = 'Failed to make identify query'
                log.exception(err)
                return as_unicode(e)

        if abort.is_set():
            return

        log.info("  Matches are: ", matches)
        print("  Matches are: ", matches)

        from calibre_plugins.yes24.worker import Worker
        workers = [Worker(url, result_queue, br, log, i, self) for i, url in enumerate(matches)]

        for w in workers:
            w.start()
            # Don't send all requests at the same time
            time.sleep(0.1)

        while not abort.is_set():
            a_worker_is_alive = False
            for w in workers:
                w.join(0.2)
                if abort.is_set():
                    break
                if w.is_alive():
                    a_worker_is_alive = True
            if not a_worker_is_alive:
                break

        return None

    def _create_query(self, log, title=None, authors=None, identifiers={}):
        isbn = check_isbn(identifiers.get('isbn', None))
        if isbn is not None:
            return YES24.SEARCH_URL + '&query=' + isbn
        tokens = []
        if title:
            title_tokens = title.split(' ')
            tokens += [quote(t.encode('euc-kr') if isinstance(t, unicode) else t) for t in title_tokens]
        if authors:
            author_tokens = authors[0].split(' ')
            tokens += [quote(t.encode('euc-kr') if isinstance(t, unicode) else t) for t in author_tokens]
        if len(tokens) == 0:
            return None
        return YES24.SEARCH_URL + '&query=' + '+'.join(tokens)

    def _parse_search_results(self, log, orig_title, orig_authors, root, matches, timeout):
        results = root.xpath('//td[@class="goods_infogrp"]')
        if not results:
            return

        for result in results:
            """
            title_node = result.xpath('.//p[contains(@class, "goods_name")]/a')[0]
            title = title_node.text_content().strip()
            if not title:
                continue

            author_node = result.xpath('.//p[@class="goods_info"]/a')[0]
            authors = []
            authors.append(author_node.text_content().strip())
            """

            # Get the detailed url to query next
            result_url = result.xpath('.//p[contains(@class, "goods_name")]/a/@href')[0]
            #log.info('**Found href: %s'%result_url)
            matches.append(self.BASE_URL + result_url)


    def download_cover(self, log, result_queue, abort,
            title=None, authors=None, identifiers={}, timeout=30):
        cached_url = self.get_cached_cover_url(identifiers)
        if cached_url is None:
            log.info('No cached cover found, running identify')
            rq = Queue()
            self.identify(log, rq, abort, title=title, authors=authors,
                    identifiers=identifiers)
            if abort.is_set():
                return
            results = []
            while True:
                try:
                    results.append(rq.get_nowait())
                except Empty:
                    break
            results.sort(key=self.identify_results_keygen(
                title=title, authors=authors, identifiers=identifiers))
            for mi in results:
                cached_url = self.get_cached_cover_url(mi.identifiers)
                if cached_url is not None:
                    break
        if cached_url is None:
            log.info('No cover found')
            return

        if abort.is_set():
            return
        br = self.browser
        log('Downloading cover from:', cached_url)
        try:
            cdata = br.open_novisit(cached_url, timeout=timeout).read()
            result_queue.put((self, cdata))
        except:
            log.exception('Failed to download cover from:', cached_url)

    def get_cached_cover_url(self, identifiers):
        yes24_id = identifiers.get('yes24', None)
        if yes24_id is None:
            isbn = identifiers.get('isbn', None)
            if isbn is not None:
                yes24_id = self.cached_isbn_to_identifier(isbn)
        if yes24_id:
            return self._identifier_to_cover_url_cache.get(yes24_id, None)


if __name__ == '__main__': # tests
    # To run these test use:
    # calibre-debug -e __init__.py
    from calibre import prints
    from calibre.ebooks.metadata.sources.test import (test_identify_plugin,
            title_test, authors_test, series_test)

    def cover_test(cover_url):
        if cover_url is not None:
            cover_url = cover_url.lower()

        def test(mi):
            mc = mi.cover_url
            if mc is not None:
                mc = mc.lower()
            if mc == cover_url:
                return True
            prints('Cover test failed. Expected: \'%s\' found: ' % cover_url, mc)
            return False

        return test

    test_identify_plugin(YES24.name,
        [

            (# A book with an ISBN
                {'identifiers':{'isbn': '9788983920683'},
                    'title':u'해리포터와 마법사의 돌', 'authors':[u'조앤.K.롤링']},
                [title_test(u'해리포터와 마법사의 돌 1', exact=True),
                 authors_test([u'조앤.K.롤링']),
                 series_test(u'해리포터', 1.0),
                 cover_test('http://image.yes24.com/goods/72289/L')]
            ),

            (# A book with no ISBN specified
                {'title':u"아투안의 무덤", 'authors':[u'어슐러 르 귄']},
                [title_test(u"아투안의 무덤", exact=True),
                 authors_test([u'어슐러 르 귄']),
                 series_test(u'어스시 전집', 2.0),
                 cover_test('http://image.yes24.com/goods/2128248/L')]
            ),

            (# A book with a YES24 id
                {'identifiers':{'yes24': '6185205'},
                    'title':u'칼의 노래', 'authors':[u'김훈']},
                [title_test(u'칼의 노래', exact=True),
                 authors_test([u'김훈']),
                 #series_test('Jack Reacher', 14.0),
                 cover_test('http://image.yes24.com/goods/6185205/L')]
            ),

            """
            (# A book with an NA cover
                {'identifiers':{'isbn':'9780451063953'},
                 'title':'The Girl Hunters', 'authors':['Mickey Spillane']},
                [title_test('The Girl Hunters', exact=True),
                 authors_test(['Mickey Spillane']),
                 cover_test(None)]
            ),
            """

        ], fail_missing_meta=False)


