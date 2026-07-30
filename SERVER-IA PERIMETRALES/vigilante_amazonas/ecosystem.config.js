// PM2 en Windows — VIGILANTE-AMAZONAS
// Uso (desde SERVER-IA PERIMETRALES):
//   pm2 start vigilante_amazonas\ecosystem.config.js
//   pm2 logs vigilante-amazonas
//   pm2 stop vigilante-amazonas
module.exports = {
  apps: [
    {
      name: "vigilante-amazonas",
      cwd: "C:\\Users\\Sistema-1\\Desktop\\ELDE\\SERVER-IA PERIMETRALES",
      script: "vigilante_amazonas\\main.py",
      interpreter: "C:\\Users\\Sistema-1\\Desktop\\ELDE\\SERVER-IA PERIMETRALES\\venv\\Scripts\\python.exe",
      // Los modelos tardan en cargar: no reiniciar por lentitud de arranque.
      min_uptime: "60s",
      max_restarts: 10,
      restart_delay: 5000,
      // SIGTERM primero (apagado limpio de main.py); matar a los 20 s.
      kill_timeout: 20000,
      autorestart: true,
      watch: false,
      out_file: "vigilante_amazonas\\logs\\pm2_out.log",
      error_file: "vigilante_amazonas\\logs\\pm2_error.log",
      merge_logs: true,
      env: {
        PYTHONIOENCODING: "utf-8",
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
