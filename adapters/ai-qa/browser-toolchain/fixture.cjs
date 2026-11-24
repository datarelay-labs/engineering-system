const http=require('node:http');
http.createServer((req,res)=>{
res.setHeader('content-type','text/html; charset=utf-8');
res.end('<label>Value<input id="value"></label><button onclick="document.querySelector(\'#result\').textContent='+(req.url==='/fault'?"\'WRONG\'":"document.querySelector(\'#value\').value")+'">Submit</button><div role="status" id="result"></div>');
}).listen(Number(process.argv[2]),'127.0.0.1');
