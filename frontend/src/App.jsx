import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from './api';

const OBJECT_TYPES = ['Todos', 'Auto', 'Moto', 'Bus', 'Camion', 'Persona'];

function formatDate(value) {
    if (!value) return 'Sin fecha';
    return new Intl.DateTimeFormat('es-BO', {
        dateStyle: 'medium',
        timeStyle: 'short',
    }).format(new Date(value));
}

function statusLabel(status) {
    return (
        {
            running: 'Activo',
            starting: 'Iniciando',
            connecting: 'Conectando',
            idle: 'Detenido',
            error: 'Error',
            offline: 'Sin señal',
        }[status] || 'Pendiente'
    );
}

function Icon({ children }) {
    return (
        <span className="icon" aria-hidden="true">
            {children}
        </span>
    );
}

function App() {
    const [page, setPage] = useState('dashboard');
    const [cameras, setCameras] = useState([]);
    const [detections, setDetections] = useState([]);
    const [statuses, setStatuses] = useState({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [selectedEvent, setSelectedEvent] = useState(null);
    const [limit, setLimit] = useState(100);

    async function loadData(silent = false, currentLimit = limit) {
        if (!silent) setLoading(true);
        try {
            const [cameraData, eventData, statusData] = await Promise.all([
                api.cameras(),
                api.detections({ limit: currentLimit }),
                api.statuses(),
            ]);
            setCameras(cameraData);
            setDetections(eventData);
            setStatuses(statusData);
            setError('');
        } catch (err) {
            setError(`No se pudo conectar con el backend: ${err.message}`);
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => {
        loadData(false, limit);
        const timer = setInterval(() => loadData(true, limit), 10000);
        return () => clearInterval(timer);
    }, [limit]);

    function handleLoadMore() {
        setLimit((prev) => prev + 100);
    }

    const activeCount = cameras.filter(
        (camera) =>
            statuses[camera.id]?.status === 'running' ||
            camera.status === 'running',
    ).length;
    const todayEvents = detections.filter(
        (event) =>
            new Date(event.detected_at || event.created).toDateString() ===
            new Date().toDateString(),
    );
    const plateCount = detections.filter(
        (event) =>
            event.plate_text && event.plate_text !== 'SIN_PLACA_DETECTADA',
    ).length;

    async function handleDeleteEvent(id) {
        if (!confirm('¿Estás seguro de eliminar este evento?')) return;
        setLoading(true);
        try {
            await api.deleteDetection(id);
            setSelectedEvent(null);
            await loadData(false);
        } catch (err) {
            alert(`Error al eliminar: ${err.message}`);
        } finally {
            setLoading(false);
        }
    }

    async function handleDeleteAllEvents() {
        if (
            !confirm(
                '¿Estás seguro de eliminar TODOS los eventos del historial? Esta acción no se puede deshacer.',
            )
        )
            return;
        setLoading(true);
        try {
            await api.deleteAllDetections();
            await loadData(false);
        } catch (err) {
            alert(`Error al eliminar: ${err.message}`);
        } finally {
            setLoading(false);
        }
    }

    const pageTitle = {
        dashboard: 'Centro de control',
        cameras: 'Cámaras',
        events: 'Registro de eventos',
    }[page];

    return (
        <div className="app-shell">
            <aside className="sidebar">
                <div className="brand">
                    <div className="brand-mark">
                        <span />
                    </div>
                    <div>
                        <strong>SENTINEL</strong>
                        <small>reconocimiento local</small>
                    </div>
                </div>

                <nav>
                    <button
                        className={
                            page === 'dashboard'
                                ? 'nav-item active'
                                : 'nav-item'
                        }
                        onClick={() => setPage('dashboard')}>
                        <Icon>⌂</Icon>Resumen
                    </button>
                    <button
                        className={
                            page === 'cameras' ? 'nav-item active' : 'nav-item'
                        }
                        onClick={() => setPage('cameras')}>
                        <Icon>◉</Icon>Cámaras <b>{cameras.length}</b>
                    </button>
                    <button
                        className={
                            page === 'events' ? 'nav-item active' : 'nav-item'
                        }
                        onClick={() => setPage('events')}>
                        <Icon>▤</Icon>Eventos <b>{detections.length}</b>
                    </button>
                </nav>
            </aside>

            <main className="main-content">
                <header className="topbar">
                    <div>
                        <span className="eyebrow">
                            MONITOREO / {page.toUpperCase()}
                        </span>
                        <h1>{pageTitle}</h1>
                    </div>
                    <div className="top-actions">
                        <span className="live-dot" />
                        Actualización automática{' '}
                        <button
                            className="refresh"
                            onClick={() => loadData()}
                            aria-label="Actualizar">
                            ↻
                        </button>
                    </div>
                </header>
                {error && (
                    <div className="error-banner">
                        {error}
                        <button onClick={() => loadData()}>Reintentar</button>
                    </div>
                )}
                {loading && !cameras.length && (
                    <div className="loading">
                        Conectando con el sistema<span>...</span>
                    </div>
                )}
                {!loading || cameras.length ? (
                    <>
                        {page === 'dashboard' && (
                            <Dashboard
                                cameras={cameras}
                                detections={detections}
                                activeCount={activeCount}
                                todayEvents={todayEvents}
                                plateCount={plateCount}
                                statuses={statuses}
                                onNavigate={setPage}
                                onSelect={setSelectedEvent}
                            />
                        )}
                        {page === 'cameras' && (
                            <Cameras
                                cameras={cameras}
                                statuses={statuses}
                                onRefresh={() => loadData(true)}
                            />
                        )}
                        {page === 'events' && (
                            <Events
                                detections={detections}
                                cameras={cameras}
                                onSelect={setSelectedEvent}
                                onDeleteAll={handleDeleteAllEvents}
                                limit={limit}
                                onLoadMore={handleLoadMore}
                            />
                        )}
                    </>
                ) : null}
            </main>
            {selectedEvent && (
                <EventModal
                    event={selectedEvent}
                    onClose={() => setSelectedEvent(null)}
                    onDelete={handleDeleteEvent}
                />
            )}
        </div>
    );
}

function Dashboard({
    cameras,
    detections,
    activeCount,
    todayEvents,
    plateCount,
    statuses,
    onNavigate,
    onSelect,
}) {
    const latest = detections.slice(0, 6);
    return (
        <>
            <section className="hero-row">
                <div>
                    <p className="section-kicker">Vigilancia en tiempo real</p>
                    <h2>Todo bajo control.</h2>
                    <p className="muted">
                        El sistema analiza los streams y solo conserva momentos
                        relevantes.
                    </p>
                </div>
                <button
                    className="primary-btn"
                    onClick={() => onNavigate('cameras')}>
                    + Configurar cámara
                </button>
            </section>
            <section className="stats-grid">
                <Stat
                    label="Eventos hoy"
                    value={todayEvents.length}
                    accent="lime"
                    detail="capturas registradas"
                />
                <Stat
                    label="Cámaras activas"
                    value={`${activeCount}/${cameras.length}`}
                    accent="blue"
                    detail="streams conectados"
                />
                <Stat
                    label="Placas leídas"
                    value={plateCount}
                    accent="yellow"
                    detail="lecturas con confianza"
                />
                <Stat
                    label="Total histórico"
                    value={detections.length}
                    accent="purple"
                    detail="eventos almacenados"
                />
            </section>
            <section className="content-grid">
                <div className="panel events-panel">
                    <div className="panel-head">
                        <div>
                            <span className="section-kicker">
                                Actividad reciente
                            </span>
                            <h3>Últimas detecciones</h3>
                        </div>
                        <button
                            className="text-btn"
                            onClick={() => onNavigate('events')}>
                            Ver todo →
                        </button>
                    </div>
                    {latest.length ? (
                        <div className="event-list">
                            {latest.map((event) => (
                                <EventRow
                                    key={event.id}
                                    event={event}
                                    onSelect={onSelect}
                                />
                            ))}
                        </div>
                    ) : (
                        <Empty text="Todavía no hay detecciones." />
                    )}
                </div>
                <div className="panel camera-panel">
                    <div className="panel-head">
                        <div>
                            <span className="section-kicker">Fuentes</span>
                            <h3>Estado de cámaras</h3>
                        </div>
                        <button
                            className="text-btn"
                            onClick={() => onNavigate('cameras')}>
                            Gestionar →
                        </button>
                    </div>
                    <div className="camera-list">
                        {cameras.slice(0, 5).map((camera) => {
                            const status =
                                statuses[camera.id]?.status ||
                                camera.status ||
                                'idle';
                            return (
                                <div className="camera-row" key={camera.id}>
                                    <span className={`camera-icon ${status}`}>
                                        <Icon>◉</Icon>
                                    </span>
                                    <div>
                                        <strong>{camera.name}</strong>
                                        <small>
                                            {camera.stream_type?.toUpperCase()}{' '}
                                            · {statusLabel(status)}
                                        </small>
                                    </div>
                                    <i className={`status-dot ${status}`} />
                                </div>
                            );
                        })}
                    </div>
                    {!cameras.length && (
                        <Empty text="Agrega tu primera cámara." />
                    )}
                </div>
            </section>
        </>
    );
}

function Stat({ label, value, detail, accent }) {
    return (
        <div className={`stat-card ${accent}`}>
            <div className="stat-top">
                <span>{label}</span>
                <i />
            </div>
            <strong>{value}</strong>
            <small>{detail}</small>
        </div>
    );
}

function EventRow({ event, onSelect }) {
    return (
        <button className="event-row" onClick={() => onSelect(event)}>
            <div className="event-thumb">
                {event.image_thumb_url || event.image_url ? (
                    <img src={event.image_thumb_url || event.image_url} />
                ) : (
                    <span>?</span>
                )}
            </div>
            <div className="event-info">
                <strong>{event.object_type}</strong>
                <small>{formatDate(event.detected_at || event.created)}</small>
            </div>
            <div
                className={`plate-badge ${event.plate_text && event.plate_text !== 'SIN_PLACA_DETECTADA' ? 'found' : ''}`}>
                {event.plate_text || 'Sin placa'}
            </div>
            <span className="confidence">
                {Math.round((event.confidence || 0) * 100)}%
            </span>
        </button>
    );
}

function Cameras({ cameras, statuses, onRefresh }) {
    const [showForm, setShowForm] = useState(false);
    const [busy, setBusy] = useState('');
    const [notice, setNotice] = useState('');
    const [liveCamera, setLiveCamera] = useState(null);
    const [streamKey, setStreamKey] = useState(Date.now());
    const [form, setForm] = useState({
        name: '',
        rtsp_url: '',
        username: '',
        password: '',
    });

    // When any camera transitions to 'running', bump the stream key
    // so the <img> src gets a fresh MJPEG connection
    const prevStatusesRef = useRef({});
    useEffect(() => {
        const prev = prevStatusesRef.current;
        for (const cam of cameras) {
            const oldState = prev[cam.id]?.status || cam.status || 'idle';
            const newState = statuses[cam.id]?.status || cam.status || 'idle';
            if (oldState !== 'running' && newState === 'running') {
                setStreamKey(Date.now());
                break;
            }
        }
        prevStatusesRef.current = { ...statuses };
    }, [statuses, cameras]);

    async function action(id, type) {
        if (
            type === 'delete' &&
            !confirm('¿Estás seguro de eliminar esta cámara?')
        )
            return;
        setBusy(`${type}-${id}`);
        setNotice('');
        try {
            const fn = {
                test: api.testCamera,
                start: api.startCamera,
                stop: api.stopCamera,
                delete: api.deleteCamera,
            }[type];
            const result = await fn(id);
            setNotice(
                type === 'test'
                    ? result.ok
                        ? `Conexión correcta · ${result.width}×${result.height} · ${result.stream_type}`
                        : result.error
                    : type === 'delete'
                      ? 'Cámara eliminada.'
                      : `Cámara ${type === 'start' ? 'iniciada' : 'detenida'}.`,
            );
            onRefresh();

            // After starting a camera, poll rapidly until we see 'running'
            if (type === 'start') {
                let attempts = 0;
                const rapidPoll = setInterval(async () => {
                    attempts++;
                    await onRefresh();
                    // Stop after 10 attempts (~15s) or if already running
                    if (attempts >= 10) clearInterval(rapidPoll);
                }, 1500);
            }
        } catch (err) {
            setNotice(err.message);
        } finally {
            setBusy('');
        }
    }
    async function submit(e) {
        e.preventDefault();
        setBusy('create');
        try {
            await api.createCamera(form);
            setForm({ name: '', rtsp_url: '', username: '', password: '' });
            setShowForm(false);
            setNotice('Cámara registrada correctamente.');
            onRefresh();
        } catch (err) {
            setNotice(err.message);
        } finally {
            setBusy('');
        }
    }
    return (
        <section>
            <div className="page-intro">
                <div>
                    <p className="section-kicker">Fuentes de video</p>
                    <h2>Control de cámaras</h2>
                    <p className="muted">
                        RTSP, MJPEG o snapshots. Las credenciales son
                        opcionales.
                    </p>
                </div>
                <button
                    className="primary-btn"
                    onClick={() => setShowForm(!showForm)}>
                    + Nueva cámara
                </button>
            </div>
            {notice && <div className="notice">{notice}</div>}
            {showForm && (
                <form className="camera-form panel" onSubmit={submit}>
                    <div className="form-title">
                        <h3>Registrar fuente</h3>
                        <span>Los campos de acceso pueden quedar vacíos.</span>
                    </div>
                    <label>
                        Nombre
                        <input
                            required
                            value={form.name}
                            onChange={(e) =>
                                setForm({ ...form, name: e.target.value })
                            }
                            placeholder="Entrada principal"
                        />
                    </label>
                    <label>
                        URL del stream
                        <input
                            required
                            value={form.rtsp_url}
                            onChange={(e) =>
                                setForm({ ...form, rtsp_url: e.target.value })
                            }
                            placeholder="rtsp://192.168.1.100:554/stream"
                        />
                    </label>
                    <div className="form-two">
                        <label>
                            Usuario{' '}
                            <input
                                value={form.username}
                                onChange={(e) =>
                                    setForm({
                                        ...form,
                                        username: e.target.value,
                                    })
                                }
                                placeholder="Opcional"
                            />
                        </label>
                        <label>
                            Contraseña{' '}
                            <input
                                type="password"
                                value={form.password}
                                onChange={(e) =>
                                    setForm({
                                        ...form,
                                        password: e.target.value,
                                    })
                                }
                                placeholder="Opcional"
                            />
                        </label>
                    </div>
                    <div className="form-actions">
                        <button
                            type="button"
                            className="ghost-btn"
                            onClick={() => setShowForm(false)}>
                            Cancelar
                        </button>
                        <button
                            className="primary-btn"
                            disabled={busy === 'create'}>
                            {busy === 'create'
                                ? 'Guardando...'
                                : 'Registrar cámara'}
                        </button>
                    </div>
                </form>
            )}
            <div className="camera-cards">
                {cameras.map((camera) => {
                    const state =
                        statuses[camera.id]?.status || camera.status || 'idle';
                    const liveUrl = `${api.url}/api/cameras/${camera.id}/live.mjpg?t=${streamKey}`;
                    return (
                        <div className="camera-card panel" key={camera.id}>
                            <div className="camera-card-top">
                                <span className={`camera-icon large ${state}`}>
                                    <Icon>◉</Icon>
                                </span>
                                <div>
                                    <h3>{camera.name}</h3>
                                    <span className="type-label">
                                        {camera.stream_type?.toUpperCase() ||
                                            'STREAM'}
                                    </span>
                                </div>
                                <i className={`status-dot ${state}`} />
                            </div>
                            {state === 'running' && (
                                <div className="live-preview">
                                    <img
                                        src={liveUrl}
                                        alt={`Vista en vivo de ${camera.name}`}
                                    />
                                    <span className="live-label">
                                        <i /> EN VIVO
                                    </span>
                                    <button
                                        onClick={() =>
                                            setLiveCamera({
                                                ...camera,
                                                liveUrl,
                                            })
                                        }>
                                        ⛶
                                    </button>
                                </div>
                            )}
                            <div className="camera-url">{camera.rtsp_url}</div>
                            <div className="camera-meta">
                                <span>
                                    <b className={`state-text ${state}`}>
                                        {statusLabel(state)}
                                    </b>
                                </span>
                                {statuses[camera.id]?.width && (
                                    <span>
                                        {statuses[camera.id].width} ×{' '}
                                        {statuses[camera.id].height}
                                    </span>
                                )}
                            </div>
                            <div className="card-actions">
                                <button
                                    onClick={() => action(camera.id, 'test')}
                                    disabled={busy === `test-${camera.id}`}>
                                    Probar conexión
                                </button>
                                {state === 'running' ? (
                                    <>
                                        <button
                                            className="danger-action"
                                            onClick={() =>
                                                action(camera.id, 'stop')
                                            }>
                                            Detener
                                        </button>
                                        <button
                                            className="live-action"
                                            onClick={() =>
                                                setLiveCamera({
                                                    ...camera,
                                                    liveUrl,
                                                })
                                            }>
                                            Ver en vivo
                                        </button>
                                    </>
                                ) : (
                                    <>
                                        <button
                                            className="start-action"
                                            onClick={() =>
                                                action(camera.id, 'start')
                                            }>
                                            Iniciar detector
                                        </button>
                                        <button
                                            className="danger-action"
                                            onClick={() =>
                                                action(camera.id, 'delete')
                                            }
                                            disabled={
                                                busy === `delete-${camera.id}`
                                            }>
                                            Eliminar
                                        </button>
                                    </>
                                )}
                            </div>
                        </div>
                    );
                })}
            </div>
            {!cameras.length && (
                <div className="panel empty-large">
                    <span>◉</span>
                    <h3>Aún no hay cámaras</h3>
                    <p>
                        Registra una cámara local o una URL pública para
                        comenzar.
                    </p>
                </div>
            )}
            {liveCamera && (
                <div
                    className="modal-backdrop live-modal-backdrop"
                    onClick={() => setLiveCamera(null)}>
                    <div
                        className="live-modal"
                        onClick={(e) => e.stopPropagation()}>
                        <button
                            className="modal-close"
                            onClick={() => setLiveCamera(null)}>
                            ×
                        </button>
                        <div className="live-modal-head">
                            <span className="live-label">
                                <i /> EN VIVO
                            </span>
                            <strong>{liveCamera.name}</strong>
                        </div>
                        <img src={liveCamera.liveUrl} alt="Stream en vivo" />
                    </div>
                </div>
            )}
        </section>
    );
}

function Events({
    detections,
    cameras,
    onSelect,
    onDeleteAll,
    limit,
    onLoadMore,
}) {
    const [type, setType] = useState('Todos');
    const [search, setSearch] = useState('');
    const [plateOnly, setPlateOnly] = useState(false);
    const filtered = useMemo(
        () =>
            detections.filter(
                (event) =>
                    (type === 'Todos' || event.object_type === type) &&
                    (!search ||
                        (event.plate_text || '')
                            .toLowerCase()
                            .includes(search.toLowerCase())) &&
                    (!plateOnly ||
                        (event.plate_text &&
                            event.plate_text !== 'SIN_PLACA_DETECTADA')),
            ),
        [detections, type, search, plateOnly],
    );
    return (
        <section>
            <div className="page-intro">
                <div>
                    <p className="section-kicker">Historial persistente</p>
                    <h2>Eventos detectados</h2>
                    <p className="muted">
                        Cada captura se guarda automáticamente en PocketBase.
                    </p>
                </div>
                <div
                    style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '15px',
                    }}>
                    {detections.length > 0 && (
                        <button className="danger-btn" onClick={onDeleteAll}>
                            Vaciar historial
                        </button>
                    )}
                    <div className="event-count">
                        {filtered.length}
                        <small> resultados</small>
                    </div>
                </div>
            </div>
            <div className="filters">
                <div className="search-box">
                    <Icon>⌕</Icon>
                    <input
                        placeholder="Buscar por placa..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                    />
                </div>
                <div className="filter-tabs">
                    {OBJECT_TYPES.map((item) => (
                        <button
                            key={item}
                            className={type === item ? 'selected' : ''}
                            onClick={() => setType(item)}>
                            {item}
                        </button>
                    ))}
                    <button
                        className={
                            plateOnly ? 'selected plate-filter' : 'plate-filter'
                        }
                        onClick={() => setPlateOnly(!plateOnly)}>
                        Con placa
                    </button>
                </div>
            </div>
            <div className="event-gallery">
                {filtered.map((event) => (
                    <button
                        className="event-card"
                        key={event.id}
                        onClick={() => onSelect(event)}>
                        <div className="gallery-image">
                            {event.image_url ? (
                                <img src={event.image_url} />
                            ) : (
                                <span>Sin imagen</span>
                            )}
                            <span
                                className={`gallery-type ${event.object_type}`}>
                                {event.object_type}
                            </span>
                        </div>
                        <div className="gallery-info">
                            <div>
                                <strong>
                                    {event.plate_text &&
                                    event.plate_text !== 'SIN_PLACA_DETECTADA'
                                        ? event.plate_text
                                        : 'Sin placa visible'}
                                </strong>
                                <small>
                                    {formatDate(
                                        event.detected_at || event.created,
                                    )}
                                </small>
                            </div>
                            <span>
                                {Math.round((event.confidence || 0) * 100)}%
                            </span>
                        </div>
                    </button>
                ))}
            </div>
            {filtered.length >= limit && (
                <div style={{ textAlign: 'center', marginTop: '25px' }}>
                    <button className="primary-btn" onClick={onLoadMore}>
                        Cargar más eventos
                    </button>
                </div>
            )}
            {!filtered.length && (
                <div className="panel empty-large">
                    <span>⌁</span>
                    <h3>No hay coincidencias</h3>
                    <p>Prueba otro filtro o espera una nueva detección.</p>
                </div>
            )}
        </section>
    );
}

function EventModal({ event, onClose, onDelete }) {
    return (
        <div className="modal-backdrop" onClick={onClose}>
            <div className="modal" onClick={(e) => e.stopPropagation()}>
                <button className="modal-close" onClick={onClose}>
                    ×
                </button>
                {event.image_url && (
                    <img className="modal-image" src={event.image_url} />
                )}
                <div className="modal-body">
                    <div
                        style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'flex-start',
                            marginBottom: '20px',
                        }}>
                        <div>
                            <span className="section-kicker">
                                Detalle de evento
                            </span>
                            <h2>{event.object_type}</h2>
                        </div>
                        <button
                            className="danger-btn-outline"
                            onClick={() => onDelete(event.id)}>
                            Eliminar Evento
                        </button>
                    </div>
                    <div className="detail-grid">
                        <div>
                            <small>Placa</small>
                            <strong>
                                {event.plate_text || 'SIN_PLACA_DETECTADA'}
                            </strong>
                        </div>
                        <div>
                            <small>Confianza</small>
                            <strong>
                                {Math.round((event.confidence || 0) * 100)}%
                            </strong>
                        </div>
                        <div>
                            <small>Fecha</small>
                            <strong>
                                {formatDate(event.detected_at || event.created)}
                            </strong>
                        </div>
                        <div>
                            <small>Tracker ID</small>
                            <strong>#{event.tracker_id ?? '—'}</strong>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}

function Empty({ text }) {
    return <div className="empty">{text}</div>;
}

export default App;
