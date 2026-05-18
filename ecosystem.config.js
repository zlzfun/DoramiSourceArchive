module.exports = {
  apps: [
    {
      name: 'dorami-source-archive-api',
      cwd: __dirname,
      script: 'src/main.py',
      interpreter: '.venv/bin/python',
      instances: 1,
      exec_mode: 'fork',
      autorestart: true,
      watch: false,
      time: true,
      max_memory_restart: '3G',
      env: {
        PYTHONPATH: 'src',
        DORAMI_CONFIG_FILE: process.env.DORAMI_CONFIG_FILE || './config/production.ini',
      },
    },
  ],
};
