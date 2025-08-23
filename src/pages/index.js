import { useEffect, useState } from 'react';
import io from 'socket.io-client';
import Head from 'next/head';
import TabButton from '../components/TabButton';
import ParameterInput from '../components/ParameterInput';

const PARAMETER_GROUPS = {
    'Kontrol PID': ['kp', 'ki', 'kd'],
    'Pengaturan Kecepatan': ['SPEED_NORMAL', 'SPEED_LAMBAT', 'SPEED_ZEBRA', 'KECEPATAN_AKSI', 'KECEPATAN_MUNDUR'],
    'Pengaturan Kemudi (Servo)': ['STEER_TENGAH', 'MAX_KIRI', 'MAX_KANAN', 'STEER_PATAH_KANAN', 'STEER_HALUS_KIRI', 'LANGKAH_STEER_KANAN_HILANG'],
    'Logika Garis': ['toleransi_error_kiri', 'toleransi_error_kanan', 'koreksi_eror', 'LEBAR_JALUR'],
    'Durasi Aksi (Detik)': ['DURASI_AKSI_KIRI', 'DURASI_AKSI_KANAN', 'DURASI_AKSI_LURUS'],
    'Parameter Lain': ['alpha_derivative', 'alpha_servo', 'max_servo_change_per_frame', 'CONFIDENCE_THRESHOLD', 'AREA_MIN_CONE', 'JARAK_BERHENTI_RAMBU', 'JARAK_STOP_ZEBRA']
};


let socket;

export default function HomePage() {
    const [activeTab, setActiveTab] = useState('dashboard');
    const [telemetry, setTelemetry] = useState(null);

    useEffect(() => {
        socket = io();

        socket.on('connect', () => {
            console.log('Terhubung ke server Socket.IO');
        });

        socket.on('telemetry_to_browser', (data) => {
            setTelemetry(data);
        });

        return () => {
            socket.disconnect();
        };
    }, []);

    const sendCommand = (action, payload = null) => socket.emit('command_from_browser', { action, payload });
    const sendParamUpdate = (name, value) => socket.emit('param_update_from_browser', { name, value });

    return (
        <>
            <Head>
                <title>ROCSU AUC Station</title>
            </Head>
            <main className="container">
                <div className="sidebar">
                    <h2>ROCSU AUC Station</h2>
                    <TabButton isActive={activeTab === 'dashboard'} onClick={() => setActiveTab('dashboard')}>Dashboard</TabButton>
                    <TabButton isActive={activeTab === 'tuning'} onClick={() => setActiveTab('tuning')}>Tuning Parameter</TabButton>
                    <TabButton isActive={activeTab === 'mission'} onClick={() => setActiveTab('mission')}>Path</TabButton>
                    <TabButton isActive={activeTab === 'diagnostics'} onClick={() => setActiveTab('diagnostics')}>Diagnostik</TabButton>
                </div>

                <div className="main-content">
                    {activeTab === 'dashboard' && (
                        <div>
                            <h2>Dashboard</h2>
                            <div className="panel-grid">
                                <img className="video-feed" src={telemetry ? `data:image/jpeg;base64,${telemetry.video_frame}`: ''} alt="Video Stream" />
                                <div>
                                    <div className="controls">
                                        <button onClick={() => sendCommand('start_motor')}>Start Motor</button>
                                        <button onClick={() => sendCommand('stop_motor')}>Stop Motor</button>
                                        <button onClick={() => sendCommand('reset_route')}>Reset Rute</button>
                                    </div>
                                    <hr />
                                    <div className="telemetry-panel">
                                        <h3>Status Kritis</h3>
                                        <p>Mode: <span>{telemetry?.status?.mode_mobil || '--'}</span></p>
                                        <p>Motor/Servo CMD: <span>{`Motor: ${telemetry?.kontrol?.motor || 0}, Servo: ${telemetry?.kontrol?.servo || 0}`}</span></p>
                                        <p>Rute: <span>{`${telemetry?.status?.rute_index || 0}/${telemetry?.status?.rute_total || 0} | Aksi: ${telemetry?.status?.aksi_rute || 'None'}`}</span></p>
                                        <p>FPS: <span>{telemetry?.fps || '--'}</span></p>
                                        <p>Error Posisi: <span>{telemetry?.status?.error_posisi || '--'}</span></p>
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}
                    
                    {activeTab === 'tuning' && (
                        <div>
                             <h2>Tuning Parameter</h2>
                            <div className="param-grid">
                                {Object.entries(PARAMETER_GROUPS).map(([groupName, params]) => (
                                    <div key={groupName} className="param-group">
                                        <h4>{groupName}</h4>
                                        {params.map(paramName => (
                                            <ParameterInput 
                                                key={paramName}
                                                name={paramName}
                                                value={telemetry?.params?.[paramName] || 0}
                                                onUpdate={sendParamUpdate}
                                            />
                                        ))}
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {activeTab === 'mission' && (
                        <div>
                            <h2>Path</h2>
                            <h4>Mode Trek</h4>
                            <input type="radio" id="mode-urban" name="track_mode" value="urban" checked={telemetry?.track_mode === 'urban'} onChange={(e) => sendCommand('update_track_mode', e.target.value)} /><label htmlFor="mode-urban">Urban</label>
                            <input type="radio" id="mode-race" name="track_mode" value="race" checked={telemetry?.track_mode === 'race'} onChange={(e) => sendCommand('update_track_mode', e.target.value)} /><label htmlFor="mode-race">Race</label>
                            
                            <h4>Urutan Rute pisahkan dengan koma</h4>
                            <input type="text" id="route-input" defaultValue={telemetry?.current_route} />
                            <button onClick={() => sendCommand('update_route', document.getElementById('route-input').value)}>Update Rute</button>
                        </div>
                    )}

                    {activeTab === 'diagnostics' && (
                         <div>
                            <h2>Diagnostik Visual</h2>
                            <div className="diagnostic-grid">
                                <div>
                                    <h4>Masking Cone (Debug)</h4>
                                    <img className="video-feed" style={{ width: '320px', height: '240px' }} src={telemetry ? `data:image/jpeg;base64,${telemetry.mask_cone_frame}` : ''} />
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            </main>
        </>
    );
}