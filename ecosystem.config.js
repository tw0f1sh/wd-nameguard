const path = require('path');

module.exports = {
  apps: [
    {
      name: 'nameguard',
      cwd: __dirname,
      script: 'app.py',
      args: '--config config.yaml',
      interpreter: path.join(__dirname, '.venv', 'bin', 'python'),
      autorestart: true,
      restart_delay: 3000,
      max_restarts: 20,
      min_uptime: '10s',
      watch: false,
      time: true,
      env: {
        PYTHONUNBUFFERED: '1'
        // Optional: WARDOGS_RCON_KEY: 'your-secret-key'
      }
    }
  ]
};
