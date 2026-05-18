const path = require('path');

module.exports = {
  apps: [
    {
      name: 'dorami-backend',
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
      },
      error_file: './logs/pm2-error.log',
      out_file: './logs/pm2-out.log',
      merge_logs: true,
    },
  ],
};
