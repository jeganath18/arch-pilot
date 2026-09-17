const http = require('http');
http.createServer((_, res) => { res.end('ambiguous'); }).listen(8080);
