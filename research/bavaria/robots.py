"""Protego 0.7 RFC 9309 adapter; transport/status handling is separate."""

from protego import Protego


class Policy:
    def __init__(self, text):
        self.parser = Protego.parse(text)

    def can_fetch(self, agent, url):
        return self.parser.can_fetch(url, agent)

    def crawl_delay(self, agent):
        rate = self.parser.request_rate(agent)
        rate_delay = rate.seconds / rate.requests if rate and rate.requests else 0
        return max(self.parser.crawl_delay(agent) or 0, rate_delay)
