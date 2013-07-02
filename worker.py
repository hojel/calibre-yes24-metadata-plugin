#!/usr/bin/env python
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai
from __future__ import (unicode_literals, division, absolute_import, print_function)

__license__   = 'GPL v3'
__copyright__ = '2013, hojel'
__docformat__ = 'restructuredtext ko'

import socket, re, datetime
from collections import OrderedDict
from threading import Thread

from lxml.html import fromstring, tostring

from calibre.ebooks.metadata.book.base import Metadata
from calibre.library.comments import sanitize_comments_html
from calibre.utils.cleantext import clean_ascii_chars

class Worker(Thread): # Get details

    '''
    Get book details from YES24 book page in a separate thread
    '''

    def __init__(self, url, result_queue, browser, log, relevance, plugin, timeout=20):
        Thread.__init__(self)
        self.daemon = True
        self.url, self.result_queue = url, result_queue
        self.log, self.timeout = log, timeout
        self.relevance, self.plugin = relevance, plugin
        self.browser = browser.clone_browser()
        self.cover_url = self.yes24_id = self.isbn = None

    def run(self):
        try:
            self.get_details()
        except:
            self.log.exception('get_details failed for url: %r'%self.url)

    def get_details(self):
        try:
            self.log.info('YES24 url: %r'%self.url)
            raw = self.browser.open_novisit(self.url, timeout=self.timeout).read().strip()
        except Exception as e:
            if callable(getattr(e, 'getcode', None)) and \
                    e.getcode() == 404:
                self.log.error('URL malformed: %r'%self.url)
                return
            attr = getattr(e, 'args', [None])
            attr = attr if attr else [None]
            if isinstance(attr[0], socket.timeout):
                msg = 'YES24 timed out. Try again later.'
                self.log.error(msg)
            else:
                msg = 'Failed to make details query: %r'%self.url
                self.log.exception(msg)
            return

        raw = raw.decode('euc-kr', errors='replace')
        #open('P:\\yes24.html', 'wb').write(raw)

        if 'HTTP 404.' in raw:
            self.log.error('URL malformed: %r'%self.url)
            return

        try:
            root = fromstring(clean_ascii_chars(raw))
        except:
            msg = 'Failed to parse YES24 details page: %r'%self.url
            self.log.exception(msg)
            return

        self.parse_details(root)

    def parse_details(self, root):
        try:
            yes24_id = self.parse_yes24_id(self.url)
        except:
            self.log.exception('Error parsing YES24 id for url: %r'%self.url)
            yes24_id = None

        try:
            (title, series, series_index) = self.parse_title_series(root)
        except:
            self.log.exception('Error parsing title and series for url: %r'%self.url)
            title = series = series_index = None

        try:
            authors = self.parse_authors(root)
        except:
            self.log.exception('Error parsing authors for url: %r'%self.url)
            authors = []

        if not title or not authors or not yes24_id:
            self.log.error('Could not find title/authors/YES24 id for %r'%self.url)
            self.log.error('YES24: %r Title: %r Authors: %r'%(yes24_id, title,
                authors))
            return

        mi = Metadata(title, authors)
        if series:
            mi.series = series
            mi.series_index = series_index
        mi.set_identifier('yes24', yes24_id)
        self.yes24_id = yes24_id

        try:
            isbn = self.parse_isbn(root)
            if isbn:
                self.isbn = mi.isbn = isbn
        except:
            self.log.exception('Error parsing ISBN for url: %r'%self.url)

        try:
            mi.comments = self.parse_comments(root)
        except:
            self.log.exception('Error parsing comments for url: %r'%self.url)

        try:
            self.cover_url = self.parse_cover(root)
        except:
            self.log.exception('Error parsing cover for url: %r'%self.url)
        mi.has_cover = bool(self.cover_url)
        mi.cover_url = self.cover_url # This is purely so we can run a test for it!!!

        try:
            mi.publisher = self.parse_publisher(root)
        except:
            self.log.exception('Error parsing publisher for url: %r'%self.url)

        try:
            mi.pubdate = self.parse_published_date(root)
        except:
            self.log.exception('Error parsing published date for url: %r'%self.url)

        mi.language = 'ko'

        mi.source_relevance = self.relevance

        if self.yes24_id:
            if self.isbn:
                self.plugin.cache_isbn_to_identifier(self.isbn, self.yes24_id)

        self.plugin.clean_downloaded_metadata(mi)
        self.result_queue.put(mi)

    def parse_yes24_id(self, url):
        return re.search('yes24.com/24/[Gg]oods/(\d+)', url).groups(0)[0]

    def parse_title_series(self, root):
        title_node = root.xpath('//h1/a')
        if not title_node:
            title_node = root.xpath('//meta[@property="og:title"]/@content')
        if not title_node:
            return (None, None, None)
        title_text = title_node[0].text.strip()

        # 시리즈
        series_node = root.xpath('//span[@class="series"]/a')
        if series_node:
            series_grp = series_node[0].text.strip().rsplit('-',1)
            series_name = series_grp[0]
            series_index = float(series_grp[1]) if len(series_grp)==2 else None
            return (title_text, series_name, series_index)
        else:
            return (title_text, None, None)

    def parse_authors(self, root):
        brief_nodes = root.xpath('//div[@id="title"]/p')
        if brief_nodes:
            bgrp = brief_nodes[0].text_content().split('|')
            return [ a.strip() for a in bgrp[0].split(',') ]

    def parse_isbn(self, root):
        detail_node = root.xpath('//dd[@class="isbn10"]/p')
        if detail_node:
            return detail_node[0].text.strip()

    def parse_publisher(self, root):
        brief_nodes = root.xpath('//div[@id="title"]/p')
        if brief_nodes:
            bgrp = brief_nodes[0].text_content().split('|')
            if len(bgrp) > 3:
                return bgrp[-2].strip()
            return bgrp[-1].strip()

    def parse_published_date(self, root):
        date_node = root.xpath('//dd[@class="pdDate"]/p')
        if date_node:
            return self._convert_date_text(date_node[0].text.strip())

    def _convert_date_text(self, date_text):
        # 2011년 8월 30일
        year_s, month_s, day_s = re.match(u'^(\d+)년 (\d+)월 (\d+)일$', date_text).group(1,2,3)
        year = int(year_s)
        month = int(month_s)
        day = int(day_s)
        return datetime.datetime(year, month, day)

    def parse_comments(self, root):
        comments = ''
        description_node = root.xpath('//div/h2/img[@title="책소개"]/../../p')
        if description_node:
            comments = tostring(description_node[0], method='html').strip()
        if comments:
            return comments

    def parse_cover(self, root):
        image_node = root.xpath('//meta[@property="og:image"]/@content')
        if image_node:
            page_url = image_node[0].strip()
            if page_url.endswith('/M'):
                page_url = page_url.replace('/M','/L')
            print("Cover URL: ", page_url)
            if self.yes24_id:
                self.plugin.cache_identifier_to_cover_url(self.yes24_id, page_url)
            # Lower our relevance factor in favour of an ISBN that has a full cover if possible
            self.relevance += 5
            return page_url

    def _is_valid_image(self, img_url):
        return True
