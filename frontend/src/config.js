import appConfig from '../app.config.json';

export const API_BASE_URL = appConfig.apiBaseUrl || '/api';
export const LOGO_PATH = appConfig.logoPath || '/brand/dorami-logo-512.png';
export const LOGO_PATHS = {
  16: appConfig.logoPaths?.['16'] || '/brand/dorami-logo-16.png',
  32: appConfig.logoPaths?.['32'] || '/brand/dorami-logo-32.png',
  48: appConfig.logoPaths?.['48'] || '/brand/dorami-logo-48.png',
  128: appConfig.logoPaths?.['128'] || '/brand/dorami-logo-128.png',
  512: appConfig.logoPaths?.['512'] || LOGO_PATH,
};
export const LOGO_SRC_SET = [32, 48, 128, 512]
  .map((size) => `${LOGO_PATHS[size]} ${size}w`)
  .join(', ');
// 哆啦美「蒙眼」彩蛋图：密码框聚焦时切换，营造「不偷看密码」的互动细节
// 默认用 256px 版本（96KB，足够 72px 在高分屏清晰）；srcSet 让低分屏退回 128px
export const LOGO_COVER_EYES_PATH =
  appConfig.logoCoverEyesPath || '/brand/dorami-logo-cover-eyes-256.png';
export const LOGO_COVER_EYES_SRC_SET =
  appConfig.logoCoverEyesSrcSet ||
  '/brand/dorami-logo-cover-eyes-128.png 128w, /brand/dorami-logo-cover-eyes-256.png 256w';
export const MCP_URL = appConfig.mcpUrl || 'http://127.0.0.1:8088/mcp';
