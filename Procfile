web: uvicorn parallax.web.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips="*"
live: python -m parallax.apps.worker.live_runner
options: python -m parallax.apps.worker.options_supervisor
crypto: python -m parallax.apps.worker.crypto_smc --poll 60
crypto-eth: python -m parallax.apps.worker.crypto_smc --symbol ETHUSD --poll 60
chat: python -m parallax.apps.worker.telegram_chat
