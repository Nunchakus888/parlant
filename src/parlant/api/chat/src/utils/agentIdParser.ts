/**
 * Parse agent_id to extract chatbot_id and session_id
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
