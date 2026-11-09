import { ref } from 'vue'

export function useRequestScope() {
  const version = ref(0)
  let controller = new AbortController()

  function capture() {
    return version.value
  }

  function isCurrent(captured) {
    return captured === version.value
  }

  function invalidate() {
    controller.abort()
    controller = new AbortController()
    version.value += 1
  }

  function signal() {
    return controller.signal
  }

  return { version, capture, isCurrent, invalidate, signal }
}
