const STREAM_CONTENT_TYPE = 'application/x-ndjson'
const TERMINAL_EVENTS = new Set(['completed', 'error', 'approval_required'])
const DELTA_EVENTS = new Set(['started', 'progress', 'result', 'rows', ...TERMINAL_EVENTS])
const V2_EVENTS = new Set(['snapshot', ...DELTA_EVENTS])
const MAX_EVENT_BYTES = 5 * 1024 * 1024

export class QueryStreamProtocolError extends Error {
  constructor(message) {
    super(message)
    this.name = 'QueryStreamProtocolError'
  }
}

export class QueryStreamInterruptedError extends Error {
  constructor(state) {
    super('查询流连接中断')
    this.name = 'QueryStreamInterruptedError'
    this.streamId = state.streamId
    this.lastSequence = state.lastSequence
    this.state = state
  }
}

export function isQueryStreamResponse(response) {
  return String(response.headers.get('content-type') || '').toLowerCase().includes(STREAM_CONTENT_TYPE)
}

function isObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function parseEvent(line) {
  let payload
  try { payload = JSON.parse(line) }
  catch { throw new QueryStreamProtocolError('流式响应包含无效 JSON') }
  if (!payload || !isObject(payload.data)) throw new QueryStreamProtocolError('流式响应协议不兼容')
  if (payload.version === '1' && DELTA_EVENTS.has(payload.event)) return payload
  if (
    payload.version === '2' &&
    V2_EVENTS.has(payload.event) &&
    typeof payload.stream_id === 'string' && payload.stream_id &&
    Number.isInteger(payload.sequence) && payload.sequence >= 0
  ) return payload
  throw new QueryStreamProtocolError('流式响应协议不兼容')
}

export function createQueryStreamState() {
  return {version: null, streamId: '', lastSequence: null, result: null, rows: [], terminal: null}
}

function terminalFromSnapshot(snapshot) {
  if (!snapshot?.terminal) return null
  if (snapshot.status === 'completed') return {event: 'completed', data: snapshot.completion || {}}
  if (snapshot.status === 'approval_required') return {event: 'approval_required', data: snapshot.approval || {}}
  return {event: 'error', data: snapshot.error || {error: {message: '查询流执行失败'}}}
}

function acceptV2(state, payload) {
  if (state.version && state.version !== '2') throw new QueryStreamProtocolError('流式响应协议版本发生变化')
  if (state.streamId && state.streamId !== payload.stream_id) throw new QueryStreamProtocolError('查询流标识发生变化')
  state.version = '2'
  state.streamId = payload.stream_id
  if (payload.event === 'snapshot') {
    const snapshot = payload.data.state
    if (!isObject(snapshot) || !Array.isArray(snapshot.rows || [])) throw new QueryStreamProtocolError('查询流快照无效')
    if (state.lastSequence !== null && payload.sequence < state.lastSequence) throw new QueryStreamProtocolError('查询流快照序号倒退')
    state.lastSequence = payload.sequence
    state.rows = [...(snapshot.rows || [])]
    state.result = snapshot.result ? {...snapshot.result, rows: [...state.rows]} : null
    state.terminal = terminalFromSnapshot(snapshot)
    return
  }
  if (state.lastSequence === null) throw new QueryStreamProtocolError('v2 查询流缺少初始快照')
  if (payload.sequence !== state.lastSequence + 1) throw new QueryStreamProtocolError('查询流事件序号不连续')
  state.lastSequence = payload.sequence
  if (payload.event === 'result') {
    state.result = {...payload.data, rows: []}
    state.rows = []
  } else if (payload.event === 'rows') {
    if (!state.result || !Array.isArray(payload.data.rows)) throw new QueryStreamProtocolError('行数据事件缺少结果元数据')
    state.rows = state.rows.concat(payload.data.rows)
    state.result = {...state.result, rows: [...state.rows]}
  } else if (TERMINAL_EVENTS.has(payload.event)) state.terminal = payload
}

function acceptV1(state, payload) {
  if (state.version && state.version !== '1') throw new QueryStreamProtocolError('流式响应协议版本发生变化')
  state.version = '1'
  if (payload.event === 'result') {
    state.result = {...payload.data, rows: []}
    state.rows = []
  } else if (payload.event === 'rows') {
    if (!state.result || !Array.isArray(payload.data.rows)) throw new QueryStreamProtocolError('行数据事件缺少结果元数据')
    state.rows = state.rows.concat(payload.data.rows)
    state.result = {...state.result, rows: [...state.rows]}
  } else if (TERMINAL_EVENTS.has(payload.event)) state.terminal = payload
}

export async function consumeQueryStream(response, onEvent = () => {}, options = {}) {
  if (!isQueryStreamResponse(response)) throw new QueryStreamProtocolError('服务器未返回 NDJSON 流')
  if (!response.body?.getReader) throw new QueryStreamProtocolError('当前浏览器不支持流式读取')
  const state = options.state || createQueryStreamState()
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const accept = payload => {
    if (payload.version === '2') acceptV2(state, payload)
    else acceptV1(state, payload)
    onEvent(payload)
  }
  try {
    while (true) {
      const {value, done} = await reader.read()
      buffer += decoder.decode(value || new Uint8Array(), {stream: !done})
      let newline
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline).trim()
        buffer = buffer.slice(newline + 1)
        if (line) accept(parseEvent(line))
      }
      if (buffer.length > MAX_EVENT_BYTES) throw new QueryStreamProtocolError('单个流式事件超过大小限制')
      if (done) break
    }
    if (buffer.trim()) accept(parseEvent(buffer.trim()))
  } catch (error) {
    if (error instanceof QueryStreamProtocolError) throw error
    if (state.version === '2' && state.lastSequence !== null) throw new QueryStreamInterruptedError(state)
    throw error
  }
  if (!state.terminal) {
    if (state.version === '2' && state.lastSequence !== null) throw new QueryStreamInterruptedError(state)
    throw new QueryStreamProtocolError('流式响应未正常结束')
  }
  if (state.terminal.event === 'completed') {
    if (!state.result) throw new QueryStreamProtocolError('流式响应缺少结果元数据')
    return {...state.result, rows: [...state.rows]}
  }
  return state.terminal.data
}

export const QUERY_STREAM_FALLBACK_STATUSES = new Set([404, 405, 406, 415, 501])
