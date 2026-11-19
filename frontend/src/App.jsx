import React, { useState, useEffect, useCallback } from 'react';
import { 
  ShieldCheck, 
  Trash2, 
  Camera, 
  CheckCircle, 
  XCircle, 
  Clock, 
  RefreshCw, 
  Lock, 
  LogOut, 
  ExternalLink, 
  Cpu, 
  Activity, 
  Sliders,
  AlertTriangle,
  Layers,
  Eye
} from 'lucide-react';

export default function App() {
  const [currentPath, setCurrentPath] = useState(window.location.pathname);

  useEffect(() => {
    const handlePopState = () => setCurrentPath(window.location.pathname);
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const navigate = (path) => {
    window.history.pushState({}, '', path);
    setCurrentPath(path);
  };

  const isAdmin = currentPath.startsWith('/admin');

  return (
    <div className="app-layout">
      {isAdmin ? (
        <AdminDashboard navigate={navigate} />
      ) : (
        <PublicDashboard navigate={navigate} />
      )}
    </div>
  );
}

/* =====================================================================
   PUBLIC DASHBOARD
===================================================================== */
function PublicDashboard({ navigate }) {
  const [stats, setStats] = useState({
    approved_captures: 0,
    approved_wet_detections: 0,
    approved_dry_detections: 0,
    approved_metal_detections: 0,
  });
  const [captures, setCaptures] = useState([]);
  const [health, setHealth] = useState(null);
  const [selectedImage, setSelectedImage] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchPublicData = useCallback(async () => {
    try {
      const [statsRes, capturesRes, healthRes] = await Promise.all([
        fetch('/api/public/stats'),
        fetch('/api/public/captures?limit=12'),
        fetch('/api/health')
      ]);

      if (statsRes.ok) setStats(await statsRes.json());
      if (capturesRes.ok) setCaptures(await capturesRes.json());
      if (healthRes.ok) setHealth(await healthRes.json());
    } catch (err) {
      console.error('Failed to fetch public data:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPublicData();
    const interval = setInterval(fetchPublicData, 6000);
    return () => clearInterval(interval);
  }, [fetchPublicData]);

  return (
    <>
      <header className="app-header">
        <div className="brand-section">
          <div className="brand-icon">
            <Trash2 size={24} />
          </div>
          <div>
            <h1 className="brand-title">Smart Waste Segregation</h1>
            <div className="brand-subtitle">AI Edge Sorting & Classification</div>
          </div>
        </div>

        <div className="nav-actions">
          {health && (
            <div className={`status-pill ${health.status === 'healthy' ? 'online' : 'offline'}`}>
              <span className="status-dot"></span>
              {health.status === 'healthy' ? 'System Online' : 'System Degraded'}
            </div>
          )}

          <button className="btn btn-secondary" onClick={() => navigate('/admin')}>
            <Lock size={15} />
            Admin Portal
          </button>
        </div>
      </header>

      <main className="main-container">
        {/* KPI Grid */}
        <div className="metrics-grid">
          <div className="metric-card">
            <div className="metric-header">
              <span>Approved Captures</span>
              <CheckCircle size={18} color="var(--accent-emerald)" />
            </div>
            <div className="metric-value">{stats.approved_captures}</div>
            <div className="metric-footer">Verified waste cycles</div>
          </div>

          <div className="metric-card">
            <div className="metric-header">
              <span>Dry Waste</span>
              <span className="badge badge-dry">DRY</span>
            </div>
            <div className="metric-value">{stats.approved_dry_detections}</div>
            <div className="metric-footer">Paper, plastic, cardboard</div>
          </div>

          <div className="metric-card">
            <div className="metric-header">
              <span>Metal Waste</span>
              <span className="badge badge-metal">METAL</span>
            </div>
            <div className="metric-value">{stats.approved_metal_detections}</div>
            <div className="metric-footer">Cans, foil, metal objects</div>
          </div>

          <div className="metric-card">
            <div className="metric-header">
              <span>Wet Waste</span>
              <span className="badge badge-wet">WET</span>
            </div>
            <div className="metric-value">{stats.approved_wet_detections}</div>
            <div className="metric-footer">Organic & biodegradable</div>
          </div>
        </div>

        {/* Live Approved Stream */}
        <div className="section-header">
          <h2 className="section-title">
            <Layers size={20} color="var(--accent-cyan)" />
            Recent Approved Classifications
          </h2>
          <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
            Strictly displaying verified items (Pending items hidden)
          </span>
        </div>

        {loading ? (
          <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-secondary)' }}>
            Loading segregation history...
          </div>
        ) : captures.length === 0 ? (
          <div className="card" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
            <Trash2 size={40} style={{ margin: '0 auto 1rem', opacity: 0.3 }} />
            <p>No verified classifications recorded yet.</p>
            <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
              Pending classifications will appear here once verified by the administrator.
            </p>
          </div>
        ) : (
          <div className="capture-grid">
            {captures.map((cap) => (
              <div key={cap.id} className="capture-item-card">
                <div 
                  className="capture-image-wrap" 
                  onClick={() => setSelectedImage(cap.image_url)}
                  title="Click to view full prediction"
                >
                  <img 
                    src={cap.image_url} 
                    alt="Classified waste" 
                    onError={(e) => { e.target.style.display = 'none'; }}
                  />
                  <div className="image-overlay-badge">
                    <Eye size={12} style={{ display: 'inline', marginRight: '4px' }} />
                    View
                  </div>
                </div>

                <div className="capture-item-info">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                      ID: {cap.id.slice(0, 8)}...
                    </span>
                    <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                      {new Date(cap.captured_at).toLocaleTimeString()}
                    </span>
                  </div>

                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '0.25rem' }}>
                    {cap.detections.length === 0 ? (
                      <span className="badge" style={{ backgroundColor: '#374151', color: '#9ca3af' }}>
                        Empty Chute (0 items)
                      </span>
                    ) : (
                      cap.detections.map((det) => (
                        <span 
                          key={det.id} 
                          className={`badge ${det.final_class === 'metal' ? 'badge-metal' : det.final_class === 'dry' ? 'badge-dry' : 'badge-wet'}`}
                        >
                          {det.final_class?.toUpperCase()} {(det.ai_confidence * 100).toFixed(0)}%
                        </span>
                      ))
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>

      {/* Image Modal */}
      {selectedImage && (
        <div className="modal-overlay" onClick={() => setSelectedImage(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="card-header">
              <span style={{ fontWeight: 600 }}>Annotated Classification Image</span>
              <button className="btn btn-secondary" onClick={() => setSelectedImage(null)}>✕</button>
            </div>
            <div style={{ padding: '1rem', background: '#000', textAlign: 'center' }}>
              <img 
                src={selectedImage} 
                alt="Detailed classification" 
                style={{ maxWidth: '100%', maxHeight: '75vh', objectFit: 'contain' }} 
              />
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/* =====================================================================
   ADMIN DASHBOARD
===================================================================== */
function AdminDashboard({ navigate }) {
  const [token, setToken] = useState(() => sessionStorage.getItem('waste_admin_token') || '');
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [isLoggingIn, setIsLoggingIn] = useState(false);

  const [dashboard, setDashboard] = useState(null);
  const [activeTab, setActiveTab] = useState('pending'); // 'pending' | 'history'
  const [pendingCaptures, setPendingCaptures] = useState([]);
  const [allCaptures, setAllCaptures] = useState([]);
  const [isCapturing, setIsCapturing] = useState(false);
  const [actionMessage, setActionMessage] = useState('');
  const [reviewNotes, setReviewNotes] = useState({});
  const [selectedImage, setSelectedImage] = useState(null);

  const handleLogin = async (e) => {
    e.preventDefault();
    setLoginError('');
    setIsLoggingIn(true);
    try {
      const res = await fetch('/api/admin/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Authentication failed');
      }
      sessionStorage.setItem('waste_admin_token', data.access_token);
      setToken(data.access_token);
      setPassword('');
    } catch (err) {
      setLoginError(err.message);
    } finally {
      setIsLoggingIn(false);
    }
  };

  const handleLogout = () => {
    sessionStorage.removeItem('waste_admin_token');
    setToken('');
  };

  const fetchAdminData = useCallback(async () => {
    if (!token) return;
    try {
      const headers = { Authorization: `Bearer ${token}` };
      const [dashRes, pendingRes, allRes] = await Promise.all([
        fetch('/api/admin/dashboard', { headers }),
        fetch('/api/admin/captures?status=pending&limit=50', { headers }),
        fetch('/api/admin/captures?limit=50', { headers })
      ]);

      if (dashRes.status === 401 || pendingRes.status === 401) {
        handleLogout();
        return;
      }

      if (dashRes.ok) setDashboard(await dashRes.json());
      if (pendingRes.ok) setPendingCaptures(await pendingRes.json());
      if (allRes.ok) setAllCaptures(await allRes.json());
    } catch (err) {
      console.error('Failed to load admin data:', err);
    }
  }, [token]);

  useEffect(() => {
    fetchAdminData();
    const interval = setInterval(fetchAdminData, 5000);
    return () => clearInterval(interval);
  }, [fetchAdminData]);

  const triggerCapture = async () => {
    setIsCapturing(true);
    setActionMessage('');
    try {
      const res = await fetch('/api/admin/camera/capture', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      if (res.ok) {
        setActionMessage(`Capture executed! Found ${data.detection_count} items.`);
        await fetchAdminData();
      } else {
        setActionMessage(`Capture error: ${data.detail || 'Failed'}`);
      }
    } catch (err) {
      setActionMessage(`Error: ${err.message}`);
    } finally {
      setIsCapturing(false);
    }
  };

  const handleApprove = async (captureId) => {
    try {
      const note = reviewNotes[captureId] || '';
      const res = await fetch(`/api/admin/captures/${captureId}/approve`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}` 
        },
        body: JSON.stringify({ admin_note: note }),
      });
      if (res.ok) {
        setActionMessage(`Capture ${captureId.slice(0, 8)} approved.`);
        fetchAdminData();
      }
    } catch (err) {
      alert(`Approval failed: ${err.message}`);
    }
  };

  const handleReject = async (captureId) => {
    try {
      const note = reviewNotes[captureId] || '';
      const res = await fetch(`/api/admin/captures/${captureId}/reject`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}` 
        },
        body: JSON.stringify({ admin_note: note }),
      });
      if (res.ok) {
        setActionMessage(`Capture ${captureId.slice(0, 8)} rejected.`);
        fetchAdminData();
      }
    } catch (err) {
      alert(`Rejection failed: ${err.message}`);
    }
  };

  const handleClassChange = async (detectionId, newClass) => {
    try {
      const res = await fetch(`/api/admin/detections/${detectionId}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          final_class: newClass,
          note: `Admin corrected classification to ${newClass}`,
        }),
      });
      if (res.ok) {
        fetchAdminData();
      }
    } catch (err) {
      alert(`Correction failed: ${err.message}`);
    }
  };

  // If not logged in, show login card
  if (!token) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '1.5rem' }}>
        <div className="card" style={{ maxWidth: '420px', width: '100%', padding: '2rem' }}>
          <div style={{ textAlign: 'center', marginBottom: '1.5rem' }}>
            <div className="brand-icon" style={{ width: '48px', height: '48px', margin: '0 auto 1rem' }}>
              <ShieldCheck size={28} />
            </div>
            <h2 style={{ fontSize: '1.5rem', color: '#fff' }}>Admin Authentication</h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: '0.25rem' }}>
              Enter administrator password to access controls
            </p>
          </div>

          {loginError && (
            <div style={{ 
              backgroundColor: 'rgba(244, 63, 94, 0.15)', 
              border: '1px solid rgba(244, 63, 94, 0.3)', 
              color: 'var(--accent-rose)', 
              padding: '0.75rem', 
              borderRadius: '6px', 
              fontSize: '0.85rem',
              marginBottom: '1rem',
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem'
            }}>
              <AlertTriangle size={16} />
              <span>{loginError}</span>
            </div>
          )}

          <form onSubmit={handleLogin}>
            <div style={{ marginBottom: '1.25rem' }}>
              <label style={{ display: 'block', fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>
                ADMIN PASSWORD
              </label>
              <input 
                type="password" 
                className="form-input" 
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••••••"
                required
              />
            </div>

            <button type="submit" className="btn btn-primary" style={{ width: '100%', padding: '0.75rem' }} disabled={isLoggingIn}>
              {isLoggingIn ? 'Authenticating...' : 'Sign In to Console'}
            </button>

            <button 
              type="button" 
              className="btn btn-secondary" 
              style={{ width: '100%', marginTop: '0.75rem' }} 
              onClick={() => navigate('/')}
            >
              Back to Public Dashboard
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <>
      <header className="app-header">
        <div className="brand-section">
          <div className="brand-icon" style={{ background: 'linear-gradient(135deg, #f59e0b, #ef4444)' }}>
            <ShieldCheck size={24} />
          </div>
          <div>
            <h1 className="brand-title">Waste Segregation Admin Console</h1>
            <div className="brand-subtitle">Model Supervision & Manual Review</div>
          </div>
        </div>

        <div className="nav-actions">
          <button className="btn btn-secondary" onClick={fetchAdminData} title="Refresh">
            <RefreshCw size={15} />
          </button>
          <button className="btn btn-secondary" onClick={() => navigate('/')}>
            Public View
          </button>
          <button className="btn btn-danger" onClick={handleLogout}>
            <LogOut size={15} />
            Logout
          </button>
        </div>
      </header>

      <main className="main-container">
        {actionMessage && (
          <div style={{ 
            backgroundColor: 'rgba(6, 182, 212, 0.15)', 
            border: '1px solid rgba(6, 182, 212, 0.3)', 
            color: 'var(--accent-cyan)', 
            padding: '0.75rem 1rem', 
            borderRadius: '6px', 
            fontSize: '0.85rem',
            marginBottom: '1rem',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center'
          }}>
            <span>{actionMessage}</span>
            <button onClick={() => setActionMessage('')} style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer' }}>✕</button>
          </div>
        )}

        {/* Admin Overview Metrics */}
        {dashboard && (
          <div className="metrics-grid">
            <div className="metric-card">
              <div className="metric-header">
                <span>Pending Review</span>
                <Clock size={18} color="var(--accent-amber)" />
              </div>
              <div className="metric-value" style={{ color: 'var(--accent-amber)' }}>
                {dashboard.pending_captures}
              </div>
              <div className="metric-footer">Awaiting classification sign-off</div>
            </div>

            <div className="metric-card">
              <div className="metric-header">
                <span>Approved Total</span>
                <CheckCircle size={18} color="var(--accent-emerald)" />
              </div>
              <div className="metric-value">{dashboard.approved_captures}</div>
              <div className="metric-footer">Dry: {dashboard.dry_detections} | Metal: {dashboard.metal_detections}</div>
            </div>

            <div className="metric-card">
              <div className="metric-header">
                <span>Rejected</span>
                <XCircle size={18} color="var(--accent-rose)" />
              </div>
              <div className="metric-value">{dashboard.rejected_captures}</div>
              <div className="metric-footer">Invalid or empty samples</div>
            </div>

            <div className="metric-card">
              <div className="metric-header">
                <span>Hardware & Model</span>
                <Cpu size={18} color="var(--accent-cyan)" />
              </div>
              <div style={{ fontSize: '0.85rem', fontFamily: 'var(--font-mono)', marginTop: '0.25rem' }}>
                <div>CAM: <span style={{ color: dashboard.camera_status.status === 'connected' ? 'var(--accent-emerald)' : 'var(--accent-amber)' }}>{dashboard.camera_status.status.toUpperCase()}</span> ({dashboard.camera_status.port})</div>
                <div style={{ marginTop: '0.2rem' }}>YOLO: <span style={{ color: dashboard.model_status.loaded ? 'var(--accent-emerald)' : 'var(--accent-rose)' }}>{dashboard.model_status.loaded ? 'LOADED' : 'UNLOADED'}</span> (conf: {dashboard.model_status.confidence_threshold})</div>
              </div>
            </div>
          </div>
        )}

        {/* Hardware Camera Control Banner */}
        <div className="card" style={{ marginBottom: '1.5rem', padding: '1rem 1.25rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <div style={{ 
                background: 'rgba(59, 130, 246, 0.15)', 
                color: 'var(--accent-blue)', 
                padding: '0.5rem', 
                borderRadius: '8px' 
              }}>
                <Camera size={22} />
              </div>
              <div>
                <div style={{ fontWeight: 600, color: '#fff' }}>ESP32-CAM Serial Capture</div>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                  Trigger manual chute image capture & local YOLO inference pipeline
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <button 
                className="btn btn-primary" 
                onClick={triggerCapture}
                disabled={isCapturing}
              >
                {isCapturing ? <RefreshCw size={15} className="spin" /> : <Camera size={15} />}
                {isCapturing ? 'Capturing & Inferring...' : 'Capture Image Now'}
              </button>
            </div>
          </div>
        </div>

        {/* Tab Switching */}
        <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem', borderBottom: '1px solid var(--border-color)', paddingBottom: '0.5rem' }}>
          <button 
            className={`btn ${activeTab === 'pending' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setActiveTab('pending')}
          >
            <Clock size={16} />
            Pending Review Queue ({pendingCaptures.length})
          </button>
          <button 
            className={`btn ${activeTab === 'history' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setActiveTab('history')}
          >
            <Layers size={16} />
            Complete Audit History ({allCaptures.length})
          </button>
        </div>

        {/* TAB 1: PENDING REVIEW QUEUE */}
        {activeTab === 'pending' && (
          <div>
            {pendingCaptures.length === 0 ? (
              <div className="card" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
                <CheckCircle size={40} style={{ margin: '0 auto 1rem', color: 'var(--accent-emerald)', opacity: 0.6 }} />
                <p>Pending queue is empty!</p>
                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
                  All incoming waste captures have been reviewed. Click "Capture Image Now" above to test another cycle.
                </p>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
                {pendingCaptures.map((cap) => (
                  <div key={cap.id} className="card">
                    <div className="card-header">
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                        <span className="status-pill pending">PENDING REVIEW</span>
                        <span style={{ fontSize: '0.8rem', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                          ID: {cap.id}
                        </span>
                      </div>
                      <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                        {new Date(cap.captured_at).toLocaleString()}
                      </span>
                    </div>

                    <div className="card-body">
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1.25rem' }}>
                        {/* Dual Images: Raw and Prediction */}
                        <div>
                          <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '0.4rem' }}>
                            RAW CHUTE CAPTURE
                          </div>
                          <div className="capture-image-wrap" onClick={() => setSelectedImage(cap.raw_image_url)}>
                            <img src={cap.raw_image_url} alt="Raw Chute" />
                          </div>
                        </div>

                        <div>
                          <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '0.4rem' }}>
                            YOLO PREDICTION & BOUNDING BOXES
                          </div>
                          <div className="capture-image-wrap" onClick={() => setSelectedImage(cap.prediction_image_url)}>
                            <img src={cap.prediction_image_url} alt="Predicted Bboxes" />
                          </div>
                        </div>

                        {/* Detections & Decision Form */}
                        <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
                          <div>
                            <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>
                              DETECTIONS TO VERIFY ({cap.detections.length})
                            </div>

                            {cap.detections.length === 0 ? (
                              <div style={{ padding: '0.75rem', background: 'var(--bg-primary)', borderRadius: '6px', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                                Zero waste items detected (Empty chute).
                              </div>
                            ) : (
                              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                                {cap.detections.map((det) => (
                                  <div 
                                    key={det.id} 
                                    style={{ 
                                      padding: '0.6rem 0.75rem', 
                                      background: 'var(--bg-primary)', 
                                      border: '1px solid var(--border-color)', 
                                      borderRadius: '6px',
                                      display: 'flex',
                                      alignItems: 'center',
                                      justifyContent: 'space-between',
                                      gap: '0.5rem'
                                    }}
                                  >
                                    <div>
                                      <div style={{ fontSize: '0.8rem', fontWeight: 600 }}>
                                        AI: <span style={{ color: det.ai_class === 'metal' ? 'var(--accent-cyan)' : 'var(--accent-amber)' }}>{det.ai_class.toUpperCase()}</span> ({(det.ai_confidence * 100).toFixed(1)}%)
                                      </div>
                                      <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                                        bbox: [{det.bbox_x}, {det.bbox_y}, {det.bbox_width}x{det.bbox_height}]
                                      </div>
                                    </div>

                                    <div>
                                      <select 
                                        className="form-select"
                                        value={det.final_class || det.ai_class}
                                        onChange={(e) => handleClassChange(det.id, e.target.value)}
                                        title="Correct classification"
                                      >
                                        <option value="dry">DRY</option>
                                        <option value="metal">METAL</option>
                                        <option value="wet">WET (Organic)</option>
                                      </select>
                                    </div>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>

                          {/* Approval / Rejection Controls */}
                          <div style={{ marginTop: '1.25rem' }}>
                            <div style={{ marginBottom: '0.75rem' }}>
                              <input 
                                type="text"
                                className="form-input"
                                placeholder="Optional reviewer note (e.g. Can detected)..."
                                value={reviewNotes[cap.id] || ''}
                                onChange={(e) => setReviewNotes({ ...reviewNotes, [cap.id]: e.target.value })}
                              />
                            </div>

                            <div style={{ display: 'flex', gap: '0.75rem' }}>
                              <button 
                                className="btn btn-success" 
                                style={{ flex: 1 }}
                                onClick={() => handleApprove(cap.id)}
                              >
                                <CheckCircle size={16} />
                                Approve
                              </button>
                              <button 
                                className="btn btn-danger" 
                                style={{ flex: 1 }}
                                onClick={() => handleReject(cap.id)}
                              >
                                <XCircle size={16} />
                                Reject
                              </button>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* TAB 2: AUDIT HISTORY */}
        {activeTab === 'history' && (
          <div className="card">
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Capture ID</th>
                    <th>Status</th>
                    <th>Timestamp</th>
                    <th>Detections</th>
                    <th>Reviewer</th>
                    <th>Admin Note</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {allCaptures.map((c) => (
                    <tr key={c.id}>
                      <td style={{ fontFamily: 'var(--font-mono)' }}>{c.id.slice(0, 8)}...</td>
                      <td>
                        <span className={`status-pill ${c.status === 'approved' ? 'online' : c.status === 'rejected' ? 'offline' : 'pending'}`}>
                          {c.status.toUpperCase()}
                        </span>
                      </td>
                      <td>{new Date(c.captured_at).toLocaleString()}</td>
                      <td>
                        <div style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap' }}>
                          {c.detections.length === 0 ? (
                            <span style={{ color: 'var(--text-muted)' }}>0 items</span>
                          ) : (
                            c.detections.map((d) => (
                              <span 
                                key={d.id} 
                                className={`badge ${(d.final_class || d.ai_class) === 'metal' ? 'badge-metal' : (d.final_class || d.ai_class) === 'dry' ? 'badge-dry' : 'badge-wet'}`}
                              >
                                {(d.final_class || d.ai_class).toUpperCase()}
                              </span>
                            ))
                          )}
                        </div>
                      </td>
                      <td>{c.reviewed_by || '—'}</td>
                      <td>{c.admin_note || '—'}</td>
                      <td>
                        <button 
                          className="btn btn-secondary" 
                          style={{ padding: '0.2rem 0.5rem', fontSize: '0.75rem' }}
                          onClick={() => setSelectedImage(c.prediction_image_url)}
                        >
                          View Image
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </main>

      {/* Modal */}
      {selectedImage && (
        <div className="modal-overlay" onClick={() => setSelectedImage(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="card-header">
              <span style={{ fontWeight: 600 }}>Image Inspection</span>
              <button className="btn btn-secondary" onClick={() => setSelectedImage(null)}>✕</button>
            </div>
            <div style={{ padding: '1rem', background: '#000', textAlign: 'center' }}>
              <img 
                src={selectedImage} 
                alt="Detailed inspection" 
                style={{ maxWidth: '100%', maxHeight: '75vh', objectFit: 'contain' }} 
              />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
