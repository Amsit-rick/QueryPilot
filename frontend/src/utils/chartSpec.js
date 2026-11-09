const allowedKinds = new Set(['bar', 'column', 'line', 'pie', 'scatter'])
const allowedKeys = new Set(['schema_version', 'kind', 'x_column', 'y_columns', 'reason', 'column_types', 'max_points'])
const allowedColumnTypes = new Set(['numeric', 'temporal', 'categorical', 'unknown'])

export function parseChartSpec(value, columns) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  if (!Array.isArray(columns) || columns.length > 100) return null
  if (Object.keys(value).some(key => !allowedKeys.has(key))) return null
  if ((value.schema_version || '1.0') !== '1.0' || !allowedKinds.has(value.kind)) return null
  if (typeof value.x_column !== 'string' || value.x_column.length > 128 || !Array.isArray(value.y_columns)) return null
  if (value.y_columns.length < 1 || value.y_columns.length > 4) return null
  if (!value.y_columns.every(column => typeof column === 'string')) return null
  if (value.kind === 'pie' || value.kind === 'scatter') {
    if (value.y_columns.length !== 1) return null
  }
  const references = [value.x_column, ...value.y_columns]
  if (!value.x_column || new Set(references).size !== references.length) return null
  const allowedColumns = new Set((columns || []).map(String))
  if (references.some(column => !allowedColumns.has(column))) return null
  const maxPoints = value.max_points ?? 100
  if (!Number.isInteger(maxPoints) || maxPoints < 1 || maxPoints > 200) return null
  if (value.reason !== undefined && (typeof value.reason !== 'string' || value.reason.length > 500)) return null
  if (value.column_types !== undefined && (
    !Array.isArray(value.column_types) ||
    value.column_types.length !== columns.length ||
    value.column_types.some(kind => !allowedColumnTypes.has(kind))
  )) return null
  return {...value, schema_version: '1.0', max_points: maxPoints}
}
