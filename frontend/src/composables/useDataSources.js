import { ref } from 'vue'

export function useDataSources(api, authFetch) {
  const datasources = ref([])
  const activeDatasource = ref('')
  const schema = ref({})
  const testResults = ref({})

  async function loadDatasources() {
    const response = await authFetch(`${api}/api/v1/data-sources`)
    if (!response.ok) return
    datasources.value = await response.json()
    if (!activeDatasource.value || !datasources.value.some(item => item.id === activeDatasource.value)) {
      activeDatasource.value = datasources.value.find(item => item.id === 'mysql-main')?.id || datasources.value[0]?.id || ''
    }
  }

  async function testDatasource(id) {
    const response = await authFetch(`${api}/api/v1/data-sources/${id}/test`, {method: 'POST'})
    testResults.value = {...testResults.value, [id]: await response.json()}
  }

  async function refreshDatasource(id) {
    const response = await authFetch(`${api}/api/v1/data-sources/${id}/refresh`, {method: 'POST'})
    if (response.ok) schema.value = {...schema.value, [id]: await response.json()}
  }

  async function browseDatasource(id) {
    const response = await authFetch(`${api}/api/v1/data-sources/${id}/schema`)
    if (response.ok) schema.value = {...schema.value, [id]: await response.json()}
  }

  function clearDatasources() {
    datasources.value = []
    activeDatasource.value = ''
    schema.value = {}
    testResults.value = {}
  }

  return { datasources, activeDatasource, schema, testResults, loadDatasources, testDatasource, refreshDatasource, browseDatasource, clearDatasources }
}
