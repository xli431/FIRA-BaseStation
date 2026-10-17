const { createServer } = require('http');
const { parse } = require('url');
const next = require('next');
const express = require('express');
const { Server } = require('socket.io');

const dev = process.env.NODE_ENV !== 'production';
const app = next({ dev });
const handle = app.getRequestHandler();

const expressApp = express();
const server = createServer(expressApp);
const io = new Server(server);

let robotSocket = null;
let tactileSocket = null;
let tactileInfo = null;

io.on('connection', (socket) => {
    console.log('connect:', socket.id);
    socket.emit('tactile_status', { connected: !!tactileSocket, ...tactileInfo });

    socket.on('register_tactile', (info) => {
        console.log(`Sensor taktil terhubung: ${socket.id}`, info);
        tactileSocket = socket;
        tactileInfo = info || {};
        io.emit('tactile_status', { connected: true, ...tactileInfo });
    });

    socket.on('tactile_from_sensor', (data) => {
        // volatile: drop frames for slow browsers instead of queueing them
        io.volatile.emit('tactile_to_browser', data);
    });

    socket.on('tactile_command_from_browser', (data) => {
        if (tactileSocket) {
            tactileSocket.emit('tactile_command_to_sensor', data);
            console.log(`Perintah taktil dikirim:`, data);
        } else {
            console.log('Perintah taktil gagal: Sensor tidak terhubung');
        }
    });

    socket.on('telemetry_from_robot', (data) => {
        io.emit('telemetry_to_browser', data);
    });

    socket.on('register_robot', () => {
        console.log(`ID: ${socket.id}`);
        robotSocket = socket;
    });

    socket.on('command_from_browser', (data) => {
        if (robotSocket) {
            robotSocket.emit('command_to_robot', data);
            console.log(`Perintah dikirim ke robot:`, data);
        } else {
            console.log('Perintah gagal: Robot tidak terhubung');
        }
    });
    
    socket.on('param_update_from_browser', (data) => {
        if (robotSocket) {
            robotSocket.emit('param_update_to_robot', data);
            console.log(`Update Param dikirim ke robot:`, data);
        } else {
            console.log('Update Param gagal: Robot tidak terhubung.');
        }
    });

    socket.on('disconnect', () => {
        console.log('Klien terputus:', socket.id);
        if (robotSocket && socket.id === robotSocket.id) {
            console.log('Robot terputus');
            robotSocket = null;
        }
        if (tactileSocket && socket.id === tactileSocket.id) {
            console.log('Sensor taktil terputus');
            tactileSocket = null;
            io.emit('tactile_status', { connected: false, ...tactileInfo });
        }
    });
});

app.prepare().then(() => {
    expressApp.all('*', (req, res) => {
        const parsedUrl = parse(req.url, true);
        handle(req, res, parsedUrl);
    });

    server.listen(3000, (err) => {
        if (err) throw err;
        console.log('> Ready on http://localhost:3000');
    });
});