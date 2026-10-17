import { useEffect, useRef, useState } from 'react';

const LABELS = ['Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz'];
const FORCE_RANGE = 20; // full bar width for Fx/Fy/Fz
const TORQUE_RANGE = 5; // full bar width for Mx/My/Mz
const HISTORY = 150;

function ForceBar({ label, value, range }) {
    const frac = Math.max(-1, Math.min(1, value / range));
    const style = frac >= 0
        ? { left: '50%', width: `${frac * 50}%` }
        : { left: `${50 + frac * 50}%`, width: `${-frac * 50}%` };
    return (
        <div className="force-row">
            <span className="force-label">{label}</span>
            <div className="force-track">
                <div className="force-zero" />
                <div className={`force-fill ${frac < 0 ? 'neg' : ''}`} style={style} />
            </div>
            <span className="force-value">{value.toFixed(2)}</span>
        </div>
    );
}

function Frame({ data, alt }) {
    return data
        ? <img className="tactile-img" src={`data:image/jpeg;base64,${data}`} alt={alt} />
        : <div className="tactile-img">Menunggu gambar...</div>;
}

function Sparkline({ values, width = 420, height = 80 }) {
    const max = Math.max(1, ...values.map(Math.abs));
    const points = values
        .map((v, i) => `${(i / (HISTORY - 1)) * width},${height / 2 - (v / max) * (height / 2 - 2)}`)
        .join(' ');
    return (
        <svg className="sparkline" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
            <line x1="0" y1={height / 2} x2={width} y2={height / 2} />
            <polyline points={points} />
        </svg>
    );
}

export default function TactilePanel({ tactile, status, onCommand }) {
    const [fzHistory, setFzHistory] = useState([]);
    const frames = useRef({ depth: '', rectify: '' });

    // Images arrive at a lower rate than force data, so keep the last ones
    if (tactile?.depth_frame) frames.current.depth = tactile.depth_frame;
    if (tactile?.rectify_frame) frames.current.rectify = tactile.rectify_frame;

    useEffect(() => {
        if (!tactile?.force) return;
        setFzHistory((h) => [...h.slice(-(HISTORY - 1)), tactile.force[2]]);
    }, [tactile]);

    const force = tactile?.force || [0, 0, 0, 0, 0, 0];
    const connected = status?.connected;

    return (
        <div>
            <h2>Sensor Taktil</h2>
            <div className="tactile-header">
                <span className={`status-dot ${connected ? 'on' : ''}`} />
                <span>{connected ? `Terhubung: ${status?.serial || '--'}` : 'Sensor tidak terhubung'}</span>
                <span>FPS: <b>{tactile?.fps ?? '--'}</b></span>
                <span className={`contact-badge ${tactile?.contact ? 'on' : ''}`}>
                    {tactile?.contact ? 'KONTAK' : 'Tidak ada kontak'}
                </span>
                <button onClick={() => onCommand('tare')} disabled={!connected}>Tare</button>
            </div>

            <div className="tactile-grid">
                <div>
                    <h4>Depth</h4>
                    <Frame data={frames.current.depth} alt="Depth" />
                </div>
                <div>
                    <h4>Rectify</h4>
                    <Frame data={frames.current.rectify} alt="Rectify" />
                </div>
                <div className="telemetry-panel">
                    <h4>Gaya &amp; Torsi</h4>
                    {LABELS.map((label, i) => (
                        <ForceBar key={label} label={label} value={force[i] || 0} range={i < 3 ? FORCE_RANGE : TORQUE_RANGE} />
                    ))}
                    <p>Depth maks: <span>{tactile?.depth_max?.toFixed(3) ?? '--'}</span></p>
                    <h4>Fz (riwayat)</h4>
                    <Sparkline values={fzHistory} />
                </div>
            </div>
        </div>
    );
}
