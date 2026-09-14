const path = require('path');

module.exports = {
  apps: [
    {
      name: 'dorami-backend-v2',
      cwd: __dirname,
      script: 'src/main.py',
      interpreter: './venv/bin/python',
      instances: 1,
      exec_mode: 'fork',
      autorestart: true,
      watch: false,
      time: true,
      max_memory_restart: '2G',
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      env: {
        PYTHONPATH: path.join(__dirname, 'src'),
        NODE_ENV: 'production',
        DORAMI_CONFIG_FILE: process.env.DORAMI_CONFIG_FILE || path.join(__dirname, 'config/production.ini'),
        // 透传系统 Chromium 路径：OS 过新（如 Ubuntu 26.04）导致 playwright 自带浏览器装不上时，
        // 装系统 chromium 后 `export PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium` 即可让
        // OpenAI News 渲染节点用它（空值时渲染器自动忽略，不影响默认行为）。
        PLAYWRIGHT_CHROMIUM_EXECUTABLE: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || '',
        // 构建来源(deploy.sh 按 tag 部署时导出;/api/runtime 透出,设置 → 关于 可核对生产版本)。
        // 空值时后端退回 git describe 自查,同样能得到结果。
        DORAMI_BUILD_REF: process.env.DORAMI_BUILD_REF || '',
        DORAMI_BUILD_SHA: process.env.DORAMI_BUILD_SHA || '',
      },
      error_file: './logs/pm2-error.log',
      out_file: './logs/pm2-out.log',
      merge_logs: true,
    },
  ],
};
