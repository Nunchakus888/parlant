export interface AgentInterface {
	id: string;
	name: string;
}

export interface CustomerInterface {
	id: string;
	name: string;
}

export interface Log {
	level: 'INFO' | 'DEBUG' | 'WARNING';
	correlation_id: string;
	message: string;
	timestamp: number;
}

export type ServerStatus = 'pending' | 'error' | 'accepted' | 'acknowledged' | 'processing' | 'typing' | 'ready';
type eventSource = 'customer' | 'customer_ui' | 'human_agent' | 'human_agent_on_behalf_of_ai_agent' | 'ai_agent' | 'system';

export interface EventInterface {
	id?: string;
	source: eventSource;
	kind: 'status' | 'message';
	correlation_id: string;
	serverStatus: ServerStatus;
	sessionId?: string;
	error?: string;
	offset: number;
	creation_utc: Date;
	data: {
		participant?: { display_name?: string }
		status?: ServerStatus;
		draft?: string;
		canned_responses?: string[];
		message: string;
		data?: { exception?: string, stage?: string };
		tags?: string;
	};
	index?: number;
}

export interface SessionInterface {
	id: string;  // session_id
	title: string;
	customer_id: string;
	creation_utc: string;
	mode?: string;
	agent_id?: string;  // Backend field name (format: chatbot_id_session_id)
	// Additional fields for /chat API
	tenant_id?: string;
	chatbot_id?: string;  // Parsed from agent_id on client side
	md5_checksum?: string;
	is_preview?: boolean;
	preview_action_book_ids?: string[];
	autofill_params?: Record<string, any>;
	timeout?: number;
}

/**
 * Parse agent_id to extract chatbot_id
 * Format: chatbot_id_session_id (chatbot_id may contain multiple underscores)
 * 
 * @param agentId - The agent_id string to parse
 * @returns Object with chatbotId and sessionId
 */
export const parseAgentId = (agentId?: string) => {
	if (!agentId) return { chatbotId: '', sessionId: '' };
	const lastUnderscoreIndex = agentId.lastIndexOf('_');
	if (lastUnderscoreIndex > 0) {
		return {
			chatbotId: agentId.substring(0, lastUnderscoreIndex),
			sessionId: agentId.substring(lastUnderscoreIndex + 1)
		};
	}
	return { chatbotId: agentId, sessionId: '' };
};

/**
 * Process session object to add chatbot_id field parsed from agent_id
 * This ensures chatbot_id is always available for use throughout the application
 * 
 * @param session - The session object from API
 * @returns Session object with chatbot_id field populated
 */
export const processSessionObject = (session: SessionInterface): SessionInterface => {
	if (!session.agent_id || session.chatbot_id) {
		// If no agent_id or chatbot_id already exists, return as is
		return session;
	}
	
	const { chatbotId } = parseAgentId(session.agent_id);
	return {
		...session,
		chatbot_id: chatbotId
	};
};

export interface SessionCsvInterface {
	Source: 'AI Agent' | 'Customer';
	Participant: string;
	Timestamp: Date;
	Message: string;
	Draft: string;
	Tags: string;
	Flag: string;
	'Correlation ID': string;
}

export interface ChatConfigInterface {
	tenant_id: string;
	chatbot_id: string;
	customer_id?: string;
	session_id?: string;
	md5_checksum?: string;
	is_preview?: boolean;
	preview_action_book_ids?: string[];
	autofill_params?: Record<string, any>;
	timeout?: number;
	session_title?: string;
}

export interface ChatRequestInterface extends ChatConfigInterface {
	message: string;
	customer_id?: string;
	session_id?: string;
}

export interface ChatResponseInterface {
	status: number;
	code: number;
	message: string;
	data?: {
		total_tokens: number;
		message: string;
		success: boolean;
		correlation_id: string;
		creation_utc: string;
		id: string;
		session_id: string;
	} | null;
}
