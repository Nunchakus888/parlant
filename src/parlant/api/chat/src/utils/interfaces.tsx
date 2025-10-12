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
	agent_id?: string;  // Backend field name
	// Additional fields for /chat API
	tenant_id?: string;
	chatbot_id?: string;  // Mapped from agent_id for chat requests
	md5_checksum?: string;
	is_preview?: boolean;
	preview_action_book_ids?: string[];
	autofill_params?: Record<string, any>;
	timeout?: number;
}

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
