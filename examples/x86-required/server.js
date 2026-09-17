const http = require('http');
http.createServer((_, res) => { res.end(JSON.stringify({ ok: true })); }).listen(8080);
