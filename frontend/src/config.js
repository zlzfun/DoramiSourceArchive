import appConfig from '../app.config.json';

export const API_BASE_URL = appConfig.apiBaseUrl || '/api';
export const LOGO_PATH = appConfig.logoPath || '/logo.png';
export const MCP_URL = appConfig.mcpUrl || 'http://127.0.0.1:8088/mcp';
