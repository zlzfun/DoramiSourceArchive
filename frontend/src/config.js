import appConfig from '../app.config.json';

export const API_BASE_URL = appConfig.apiBaseUrl || '/api';
export const LOGO_PATH = appConfig.logoPath || '/brand/dorami-logo-512.png';
export const LOGO_PATHS = {
  16: appConfig.logoPaths?.['16'] || '/brand/dorami-logo-16.png',
  32: appConfig.logoPaths?.['32'] || '/brand/dorami-logo-32.png',
  48: appConfig.logoPaths?.['48'] || '/brand/dorami-logo-48.png',
  128: appConfig.logoPaths?.['128'] || '/brand/dorami-logo-128.png',
  512: appConfig.logoPaths?.['512'] || LOGO_PATH,
  master: appConfig.logoPaths?.master || '/brand/dorami-logo-master.png',
};
export const LOGO_SRC_SET = [32, 48, 128, 512]
  .map((size) => `${LOGO_PATHS[size]} ${size}w`)
  .join(', ');
export const MCP_URL = appConfig.mcpUrl || 'http://127.0.0.1:8088/mcp';
