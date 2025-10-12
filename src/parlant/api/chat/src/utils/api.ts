export const BASE_URL = import.meta.env.VITE_BASE_URL || '';

/**
 * Encodes path segments in URL endpoint
 * Example: "sessions/test:id/events" -> "sessions/test%3Aid/events"
 */
const encodeEndpoint = (endpoint: string): string => {
	// Split by / and encode each segment, then rejoin
	return endpoint.split('/').map(segment => {
		// Don't encode empty segments or query strings
		if (!segment || segment.includes('?') || segment.includes('=')) {
			return segment;
		}
		// Check if segment needs encoding (contains special characters)
		if (segment !== encodeURIComponent(segment)) {
			return encodeURIComponent(segment);
		}
		return segment;
	}).join('/');
};

const request = async (url: string, options: RequestInit = {}) => {
	try {
		const response = await fetch(url, options);
		if (!response.ok) {
			throw new Error(`HTTP error! Status: ${response.status}`);
		}
		if (options.method === 'PATCH' || options.method === 'DELETE') return;
		return await response.json();
	} catch (error) {
		console.error('Fetch error:', error);
		throw error;
	}
};

export const getData = async (endpoint: string) => {
	const encodedEndpoint = encodeEndpoint(endpoint);
	return request(`${BASE_URL}/${encodedEndpoint}`);
};

export const postData = async (endpoint: string, data?: object) => {
	const encodedEndpoint = encodeEndpoint(endpoint);
	return request(`${BASE_URL}/${encodedEndpoint}`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
		},
		body: JSON.stringify(data),
	});
};

export const patchData = async (endpoint: string, data: object) => {
	const encodedEndpoint = encodeEndpoint(endpoint);
	return request(`${BASE_URL}/${encodedEndpoint}`, {
		method: 'PATCH',
		headers: {
			'Content-Type': 'application/json',
		},
		body: JSON.stringify(data),
	});
};

export const deleteData = async (endpoint: string) => {
	const encodedEndpoint = encodeEndpoint(endpoint);
	return request(`${BASE_URL}/${encodedEndpoint}`, {
		method: 'DELETE',
	});
};
