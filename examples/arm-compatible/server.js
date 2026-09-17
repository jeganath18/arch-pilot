const express = require('express');
const app = express();
app.get('/', (_, res) => res.json({ ok: true, architecture: process.arch, message: 'ArchPilot ARM64 demo' }));
app.listen(process.env.PORT || 8080);
