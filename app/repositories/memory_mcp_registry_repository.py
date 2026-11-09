"""In-memory MCP registry used by local SQLite mode and tests."""


class InMemoryMcpRegistryRepository:
    def __init__(self):
        self.servers = {}

    def clear(self) -> None:
        self.servers.clear()

    def create(self, server):
        self.servers[server.id] = server
        return server

    def update(self, server):
        self.servers[server.id] = server
        return server

    def get(self, server_id: str, tenant_id: str):
        server = self.servers.get(server_id)
        return server if server and server.tenant_id == tenant_id else None

    def list(self, tenant_id: str):
        return sorted(
            (server for server in self.servers.values() if server.tenant_id == tenant_id),
            key=lambda server: server.name.casefold(),
        )

    def delete(self, server_id: str, tenant_id: str) -> bool:
        if not self.get(server_id, tenant_id):
            return False
        del self.servers[server_id]
        return True

