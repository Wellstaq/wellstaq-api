"""Small deterministic Redis double for OTP and security-service tests."""


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.expiry = {}
        self.now = 0

    def advance(self, seconds):
        self.now += seconds

    def _expire(self, key):
        if key in self.expiry and self.expiry[key] <= self.now:
            self.values.pop(key, None)
            self.expiry.pop(key, None)

    async def get(self, key):
        self._expire(key)
        return self.values.get(key)

    async def set(self, key, value, ex=None, keepttl=False, nx=False):
        self._expire(key)
        if nx and key in self.values:
            return None
        self.values[key] = value
        if ex is not None:
            self.expiry[key] = self.now + ex
        elif not keepttl:
            self.expiry.pop(key, None)
        return True

    async def delete(self, *keys):
        count = 0
        for key in keys:
            self._expire(key)
            count += int(key in self.values)
            self.values.pop(key, None)
            self.expiry.pop(key, None)
        return count

    async def ttl(self, key):
        self._expire(key)
        if key not in self.values:
            return -2
        return int(self.expiry[key] - self.now) if key in self.expiry else -1

    async def aclose(self):
        pass
