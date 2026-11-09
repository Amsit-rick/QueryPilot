from dataclasses import replace


class InMemoryModelOperationsRepository:
    def __init__(self):
        self.providers = {}
        self.prompts = {}

    def clear(self):
        self.providers.clear()
        self.prompts.clear()

    def create_provider(self, provider):
        if provider.is_default:
            self.clear_default_provider(provider.tenant_id, provider.id)
        self.providers[provider.id] = provider
        return provider

    def update_provider(self, provider):
        if provider.is_default:
            self.clear_default_provider(provider.tenant_id, provider.id)
        self.providers[provider.id] = provider
        return provider

    def get_provider(self, provider_id, tenant_id):
        provider = self.providers.get(provider_id)
        return provider if provider and provider.tenant_id == tenant_id else None

    def list_providers(self, tenant_id):
        return sorted((item for item in self.providers.values() if item.tenant_id == tenant_id), key=lambda item: (not item.is_default, item.name.lower()))

    def clear_default_provider(self, tenant_id, except_id=""):
        for provider_id, provider in list(self.providers.items()):
            if provider.tenant_id == tenant_id and provider.id != except_id and provider.is_default:
                self.providers[provider_id] = replace(provider, is_default=False)

    def create_prompt(self, prompt):
        if prompt.active:
            for current_id, current in list(self.prompts.items()):
                if current.tenant_id == prompt.tenant_id and current.name == prompt.name:
                    self.prompts[current_id] = replace(current, active=False)
        self.prompts[prompt.id] = prompt
        return prompt

    def get_prompt(self, prompt_id, tenant_id):
        prompt = self.prompts.get(prompt_id)
        return prompt if prompt and prompt.tenant_id == tenant_id else None

    def list_prompts(self, tenant_id, name=""):
        values = [item for item in self.prompts.values() if item.tenant_id == tenant_id and (not name or item.name == name)]
        return sorted(values, key=lambda item: (item.name, -item.version))

    def next_prompt_version(self, tenant_id, name):
        versions = [item.version for item in self.prompts.values() if item.tenant_id == tenant_id and item.name == name]
        return max(versions, default=0) + 1

    def activate_prompt(self, prompt_id, tenant_id):
        target = self.get_prompt(prompt_id, tenant_id)
        if not target:
            return None
        for current_id, prompt in list(self.prompts.items()):
            if prompt.tenant_id == tenant_id and prompt.name == target.name:
                self.prompts[current_id] = replace(prompt, active=current_id == prompt_id)
        return self.prompts[prompt_id]
