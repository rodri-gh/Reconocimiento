const API_URL = (import.meta.env.VITE_API_URL || (import.meta.env.PROD ? '' : 'http://127.0.0.1:8000')).replace(/\/$/, '')

async function request(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!response.ok) {
    const message = await response.text()
    throw new Error(message || `Error ${response.status}`)
  }
  if (response.status === 204) return null
  return response.json()
}

export const api = {
  url: API_URL,
  cameras: () => request('/api/cameras'),
  createCamera: (data) => request('/api/cameras', { method: 'POST', body: JSON.stringify(data) }),
  updateCamera: (id, data) => request(`/api/cameras/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteCamera: (id) => request(`/api/cameras/${id}`, { method: 'DELETE' }),
  testCamera: (id) => request(`/api/cameras/${id}/test`, { method: 'POST' }),
  startCamera: (id) => request(`/api/cameras/${id}/start`, { method: 'POST' }),
  stopCamera: (id) => request(`/api/cameras/${id}/stop`, { method: 'POST' }),
  statuses: () => request('/api/status'),
  detections: (params = {}) => {
    const query = new URLSearchParams()
    if (params.camera) query.set('camera', params.camera)
    if (params.object_type) query.set('object_type', params.object_type)
    query.set('limit', params.limit || 100)
    return request(`/api/detections?${query}`)
  },
  deleteDetection: (id) => request(`/api/detections/${id}`, { method: 'DELETE' }),
  deleteAllDetections: () => request('/api/detections', { method: 'DELETE' }),
}
