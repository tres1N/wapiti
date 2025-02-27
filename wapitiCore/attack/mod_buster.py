#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# This file is part of the Wapiti project (https://wapiti.sourceforge.io)
# Copyright (C) 2014-2021 Nicolas Surribas
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
import asyncio

from httpx import RequestError

from wapitiCore.main.log import log_red, log_verbose
from wapitiCore.attack.attack import Attack
from wapitiCore.net.web import Request


class ModuleBuster(Attack):
    """
    Brute force paths on the web-server to discover hidden files and directories.
    """

    PAYLOADS_FILE = "busterPayloads.txt"

    name = "buster"

    do_get = False
    do_post = False

    def __init__(self, crawler, persister, attack_options, stop_event):
        Attack.__init__(self, crawler, persister, attack_options, stop_event)
        self.known_dirs = []
        self.known_pages = []
        self.new_resources = []
        self.network_errors = 0

    async def check_path(self, url):
        page = Request(url)
        try:
            response = await self.crawler.async_send(page)
        except RequestError:
            self.network_errors += 1
            return False

        if response.redirection_url:
            loc = response.redirection_url
            if response.is_directory_redirection:
                log_red(f"Found webpage {loc}")
                self.new_resources.append(loc)
            else:
                log_red(f"Found webpage {page.path}")
                self.new_resources.append(page.path)
            return True

        if response.status not in [403, 404, 429]:
            log_red(f"Found webpage {page.path}")
            self.new_resources.append(page.path)
            return True

        return False

    async def test_directory(self, path: str):
        log_verbose(f"[¨] Testing directory {path}")

        test_page = Request(path + "does_n0t_exist.htm")
        try:
            response = await self.crawler.async_send(test_page)
        except RequestError:
            self.network_errors += 1
            return

        if response.status not in [403, 404]:
            # we don't want to deal with this at the moment
            return

        tasks = set()
        pending_count = 0
        payload_iterator = iter(self.payloads)

        while True:
            if pending_count < self.options["tasks"] and not self._stop_event.is_set():
                try:
                    candidate, __ = next(payload_iterator)
                except StopIteration:
                    pass
                else:
                    url = path + candidate
                    if url not in self.known_dirs and url not in self.known_pages and url not in self.new_resources:
                        task = asyncio.create_task(self.check_path(url))
                        tasks.add(task)

            if not tasks:
                break

            done_tasks, pending_tasks = await asyncio.wait(
                tasks,
                timeout=0.01,
                return_when=asyncio.FIRST_COMPLETED
            )
            pending_count = len(pending_tasks)
            for task in done_tasks:
                try:
                    await task
                except RequestError:
                    self.network_errors += 1
                tasks.remove(task)

            if self._stop_event.is_set():
                for task in pending_tasks:
                    task.cancel()
                    tasks.remove(task)

    async def attack(self, request: Request):
        self.finished = True
        if not self.do_get:
            return

        # First we make a list of unique webdirs and webpages without parameters
        async for resource in self.persister.get_links(attack_module=self.name):
            path = resource.path
            if path.endswith("/"):
                if path not in self.known_dirs:
                    self.known_dirs.append(path)
            else:
                if path not in self.known_pages:
                    self.known_pages.append(path)

        # Then for each known webdirs we look for unknown webpages inside
        for current_dir in self.known_dirs:
            if self._stop_event.is_set():
                break
            await self.test_directory(current_dir)

        # Finally, for each discovered webdirs we look for more webpages
        while self.new_resources and not self._stop_event.is_set():
            current_res = self.new_resources.pop(0)
            if current_res.endswith("/"):
                # Mark as known then explore
                self.known_dirs.append(current_res)
                await self.test_directory(current_res)
            else:
                self.known_pages.append(current_res)
