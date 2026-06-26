import appConfig from '../app.config.json';

export const API_BASE_URL = appConfig.apiBaseUrl || '/api';
export const LOGO_PATH = appConfig.logoPath || '/brand/dorami-logo-512.png';
export const LOGO_PATHS = {
  16: appConfig.logoPaths?.['16'] || '/brand/dorami-logo-16.png',
  32: appConfig.logoPaths?.['32'] || '/brand/dorami-logo-32.png',
  48: appConfig.logoPaths?.['48'] || '/brand/dorami-logo-48.png',
  128: appConfig.logoPaths?.['128'] || '/brand/dorami-logo-128.png',
  // 256：登录 hero（72px）在高分屏的候选。logo 为不透明 RGB，故用 JPEG（q85，31KB）
  // 替代旧的 512px PNG（279KB）——补上 128w→512w 之间的断档，Retina 不再错拉大图。
  256: appConfig.logoPaths?.['256'] || '/brand/dorami-logo-256.jpg',
  512: appConfig.logoPaths?.['512'] || LOGO_PATH,
};
export const LOGO_SRC_SET = [32, 48, 128, 256, 512]
  .map((size) => `${LOGO_PATHS[size]} ${size}w`)
  .join(', ');
// 哆啦美「蒙眼」彩蛋图：密码框聚焦时切换，营造「不偷看密码」的互动细节。
// 256px 用 JPEG（不透明 RGB，30KB，原 PNG 98KB）；srcSet 让低分屏退回 128px PNG。
export const LOGO_COVER_EYES_PATH =
  appConfig.logoCoverEyesPath || '/brand/dorami-logo-cover-eyes-256.jpg';
export const LOGO_COVER_EYES_SRC_SET =
  appConfig.logoCoverEyesSrcSet ||
  '/brand/dorami-logo-cover-eyes-128.png 128w, /brand/dorami-logo-cover-eyes-256.jpg 256w';
export const MCP_URL = appConfig.mcpUrl || 'http://127.0.0.1:8088/mcp';
