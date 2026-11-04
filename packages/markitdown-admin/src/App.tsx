import { FormEvent, isValidElement, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, NavLink, Navigate, Outlet, Route, Routes, useNavigate, useParams } from 'react-router-dom';
import { api, ApiKey, Conversion, Plan, records, User, value } from './api';

const adminRole = (user: User) => [user.role, ...(user.roles ?? [])].some((role) => ['admin', 'owner', 'superadmin'].includes((role ?? '').toLowerCase()));
const displayName = (user?: User) => user?.name || user?.display_name || user?.email?.split('@')[0] || 'Cuenta';
const errText = (error: unknown) => error instanceof Error ? error.message : 'Ocurrió un error inesperado.';
const dateLabel = (date?: string) => date ? new Intl.DateTimeFormat('es-MX', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(date)) : '—';
const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
const planRecord = (user: User): Record<string, unknown> => user.plan && typeof user.plan === 'object' ? user.plan as Record<string, unknown> : {};
function userPlanLimit(user: User, ...names: string[]): number | undefined {
  const plan = planRecord(user); const limits = (plan.limits && typeof plan.limits === 'object' ? plan.limits : (user as User & { limits?: Record<string, unknown> }).limits) as Record<string, unknown> | undefined;
  for (const name of names) {
    const value = plan[name] ?? limits?.[name];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
  }
  return undefined;
}
function userFileLimit(user: User): { bytes: number; label: string } | undefined {
  const bytes = userPlanLimit(user, 'max_file_bytes', 'maxFileBytes', 'max_file_size_bytes', 'max_input_bytes');
  if (bytes !== undefined) return { bytes, label: `${(bytes / (1024 * 1024)).toLocaleString(undefined, { maximumFractionDigits: 2 })} MiB` };
  const mb = userPlanLimit(user, 'max_file_mb', 'max_file_size_mb', 'max_upload_mb', 'maxFileMb');
  if (mb !== undefined) return { bytes: mb * 1_000_000, label: `${mb} MB` };
  return undefined;
}
function userPlanHasOcr(user: User) {
  const plan = planRecord(user); const limits = (plan.limits && typeof plan.limits === 'object' ? plan.limits : {}) as Record<string, unknown>;
  const account = user as User & { ocrEnabled?: boolean; ocr_enabled?: boolean };
  const features = plan.features as string[] | undefined;
  const flag = account.ocrEnabled ?? account.ocr_enabled ?? plan.ocrEnabled ?? plan.ocr_enabled ?? limits.ocrEnabled ?? limits.ocr_enabled ?? features?.some((feature) => feature.toLowerCase() === 'ocr');
  return flag === true;
}
function configuredPlanLimits(plan: Plan): [string, unknown][] {
  const topLevel = Object.entries(plan).filter(([key]) => key !== 'limits' && /limit|max|quota|requests|ocr|batch/i.test(key));
  const nested = plan.limits && typeof plan.limits === 'object' ? Object.entries(plan.limits) : [];
  return [...topLevel, ...nested];
}
const quotaFields = [
  ['max_conversions_month', 'Conversiones por mes'],
  ['max_input_bytes_month', 'Bytes de entrada por mes'],
  ['max_pages_month', 'Unidades de página por mes'],
  ['requests_per_minute', 'Solicitudes por minuto'],
  ['max_file_bytes', 'Máximo de bytes por archivo'],
  ['max_batch_files', 'Archivos por lote'],
] as const;
function planIsActive(plan: Plan) { return plan.active === true || plan.is_active === true; }
function quotaValue(plan: Record<string, unknown>, key: string) {
  const limits = plan.limits && typeof plan.limits === 'object' ? plan.limits as Record<string, unknown> : {};
  const keys = key === 'max_batch_files' ? ['max_batch_files', 'batch_max_files'] : [key];
  for (const candidate of keys) if (plan[candidate] !== undefined && plan[candidate] !== null) return plan[candidate];
  for (const candidate of keys) if (limits[candidate] !== undefined && limits[candidate] !== null) return limits[candidate];
  return undefined;
}
function quotaMatrix(plan: Record<string, unknown>) {
  const missing = quotaFields.filter(([key]) => {
    const raw = quotaValue(plan, key);
    return raw === null || raw === undefined || raw === '' || !Number.isFinite(Number(raw)) || Number(raw) < 0;
  }).map(([key]) => key);
  const fileBytes = Number(quotaValue(plan, 'max_file_bytes'));
  const batchFiles = Number(quotaValue(plan, 'max_batch_files'));
  const requestRate = Number(quotaValue(plan, 'requests_per_minute'));
  if (!(fileBytes > 0 && fileBytes <= 100 * 1024 * 1024) && !missing.includes('max_file_bytes')) missing.push('max_file_bytes');
  if (!(batchFiles >= 1) && !missing.includes('max_batch_files')) missing.push('max_batch_files');
  if (!(requestRate > 0) && !missing.includes('requests_per_minute')) missing.push('requests_per_minute');
  const ocrEnabled = quotaValue(plan, 'ocr_enabled');
  const ocrUnits = quotaValue(plan, 'max_ocr_units_month');
  if (typeof ocrEnabled !== 'boolean') missing.push('ocr_enabled');
  else if (ocrEnabled && (ocrUnits === null || ocrUnits === undefined || ocrUnits === '' || !Number.isFinite(Number(ocrUnits)) || Number(ocrUnits) < 0)) missing.push('max_ocr_units_month');
  return { complete: missing.length === 0, missing, ocrEnabled };
}
const userLimitFields = [
  ['max_conversions_month', 'Conversiones por mes', 'conversiones'],
  ['max_input_bytes_month', 'Entrada por mes', 'MiB'],
  ['max_pages_month', 'Unidades de página por mes', 'páginas'],
  ['requests_per_minute', 'Solicitudes por minuto', 'requests/min'],
  ['max_file_bytes', 'Máximo por archivo', 'MiB'],
] as const;
type LimitOverrideDraft = { mode: 'inherit' | 'explicit'; value: string };
type UserLimitsDraft = Record<(typeof userLimitFields)[number][0] | 'max_ocr_units_month', LimitOverrideDraft> & { ocr_enabled: { mode: 'inherit' | 'enabled' | 'disabled'; value: string } };
function bytesToMiBInput(raw: unknown) {
  if (raw === null || raw === undefined || raw === '') return '';
  const bytes = Number(raw);
  if (!Number.isFinite(bytes)) return '';
  return String(Number((bytes / (1024 * 1024)).toFixed(8)));
}
function initialUserLimitDraft(user: User): UserLimitsDraft {
  const rawSource: Record<string, unknown> = user.limit_overrides ?? user.limitOverrides ?? (user as User & { limits_overrides?: Record<string, unknown> }).limits_overrides ?? (user as User & { overrides?: Record<string, unknown> }).overrides ?? {};
  const source = rawSource.limits && typeof rawSource.limits === 'object' ? rawSource.limits as Record<string, unknown> : rawSource;
  const draft = {} as UserLimitsDraft;
  for (const [key] of userLimitFields) {
    const value = source[key];
    const isBytes = key === 'max_input_bytes_month' || key === 'max_file_bytes';
    draft[key] = value === null || value === undefined
      ? { mode: 'inherit', value: '' }
      : { mode: 'explicit', value: isBytes ? bytesToMiBInput(value) : String(value) };
  }
  const ocrValue = source.ocr_enabled;
  draft.ocr_enabled = ocrValue === true ? { mode: 'enabled', value: '' } : ocrValue === false ? { mode: 'disabled', value: '' } : { mode: 'inherit', value: '' };
  const ocrUnits = source.max_ocr_units_month;
  draft.max_ocr_units_month = ocrUnits === null || ocrUnits === undefined ? { mode: 'inherit', value: '' } : { mode: 'explicit', value: String(ocrUnits) };
  return draft;
}
function effectiveLimitLabel(value: unknown, key: string) {
  if (value === undefined || value === null) return 'No disponible';
  if (typeof value === 'boolean') return value ? 'Habilitado' : 'Deshabilitado';
  if (['max_file_bytes', 'max_input_bytes_month'].includes(key) && Number.isFinite(Number(value))) return `${(Number(value) / (1024 * 1024)).toLocaleString(undefined, { maximumFractionDigits: 2 })} MiB`;
  return String(value);
}

function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
    file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h8"/></>,
    users: <><path d="M16 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="10" cy="7" r="4"/><path d="M20 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></>,
    chart: <><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-5 5"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.7a8 8 0 0 1-1.7 1l-.3 1.8h-2.8l-.3-1.8a8 8 0 0 1-1.7-1l-1.7.7-1.4-2.4 1.4-1.1a7 7 0 0 1 0-2l-1.4-1.1 1.4-2.4 1.7.7a8 8 0 0 1 1.7-1l.3-1.8h2.8l.3 1.8a8 8 0 0 1 1.7 1l1.7-.7 1.4 2.4-1.4 1.1a7 7 0 0 1 0 2Z"/></>,
    search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
    plus: <><path d="M12 5v14M5 12h14"/></>,
    arrow: <><path d="M7 17 17 7M7 7h10v10"/></>,
    logout: <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/></>,
    copy: <><rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></>,
    download: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></>,
    shield: <><path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z"/><path d="m9 12 2 2 4-4"/></>,
    clock: <><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></>,
  };
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name] ?? paths.file}</svg>;
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authError, setAuthError] = useState('');
  const [mobileNav, setMobileNav] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    let active = true;
    api.me().then((data) => { if (active) setUser(('user' in (data as object) ? (data as { user?: User }).user : data) ?? null); })
      .catch((error) => { if (active) setAuthError(errText(error)); })
      .finally(() => { if (active) setAuthLoading(false); });
    return () => { active = false; };
  }, []);

  const logout = async () => {
    try { await api.logout(); } catch { /* local session is cleared even if the endpoint is unavailable */ }
    sessionStorage.removeItem('markitdown-api-key');
    setUser(null); navigate('/login');
  };
  const onLogin = (nextUser: User) => { setUser(nextUser); setAuthError(''); navigate('/dashboard'); };

  if (authLoading) return <div className="screen-loading"><span className="spinner" />Comprobando sesión…</div>;

  return <Routes>
    <Route path="/login" element={user ? <Navigate to="/dashboard" replace /> : <LoginPage onLogin={onLogin} initialError={authError} />} />
    <Route path="/accept-invitation" element={<AcceptInvitationPage />} />
    <Route path="/" element={user ? <Shell user={user} onLogout={logout} mobileNav={mobileNav} setMobileNav={setMobileNav} /> : <Navigate to="/login" replace />}>
      <Route index element={<Navigate to="/dashboard" replace />} />
      <Route path="dashboard" element={<Dashboard user={user!} />} />
      <Route path="convert" element={<ConvertPage user={user!} />} />
      <Route path="plans" element={<PublicPlansPage />} />
      <Route path="users" element={adminRole(user!) ? <UsersPage /> : <Navigate to="/dashboard" replace />} />
      <Route path="users/:id" element={adminRole(user!) ? <UserDetailPage /> : <Navigate to="/dashboard" replace />} />
      <Route path="reports/consumption" element={adminRole(user!) ? <ReportsPage /> : <Navigate to="/dashboard" replace />} />
      <Route path="profile" element={<ProfilePage user={user!} onUserChange={setUser} onLogout={logout} />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Route>
  </Routes>;
}

function LoginPage({ onLogin, initialError }: { onLogin: (user: User) => void; initialError: string }) {
  const [email, setEmail] = useState(''); const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(initialError);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const result = await api.login(email.trim(), password);
      const user = result.user ?? await api.me().then((data) => ('user' in (data as object) ? (data as { user: User }).user : data as User));
      onLogin(user);
    } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  };
  return <main className="login-layout">
    <section className="login-aside"><div className="brand brand-light"><span className="brand-mark">M</span><span>markitdown</span></div><div className="aside-copy"><div className="eyebrow light">DOCUMENT WORKFLOW</div><h1>De tus documentos<br />a conocimiento útil.</h1><p>Convierte contenido a Markdown y administra tu actividad desde un solo lugar.</p><div className="aside-note"><span className="pulse-dot" /> Servicio operativo <span className="divider-dot">·</span> Procesamiento seguro</div></div><div className="aside-ornament"><div className="orb orb-one"/><div className="orb orb-two"/><div className="orbit orbit-one"/><div className="orbit orbit-two"/><span className="orb-core">M</span></div><small className="aside-foot">MARKITDOWN · CONVERSIÓN INTELIGENTE</small></section>
    <section className="login-main"><div className="login-form-wrap"><div className="login-mobile-brand brand"><span className="brand-mark">M</span><span>markitdown</span></div><div className="eyebrow">BIENVENIDO DE NUEVO</div><h2>Inicia sesión</h2><p className="muted">Accede a tu espacio de trabajo.</p>{error && <div className="alert alert-error" role="alert">{error}</div>}
      <form className="form-stack login-form" onSubmit={submit}>
        <label>Correo electrónico<input autoComplete="username" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="nombre@empresa.com" /></label>
        <label>Contraseña<input autoComplete="current-password" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••••••" /></label>
        <button className="button button-primary button-full" disabled={busy}>{busy ? <><span className="spinner spinner-light"/> Validando…</> : <>Entrar <span aria-hidden="true">→</span></>}</button>
      </form><div className="login-security"><Icon name="shield" size={16}/><span>Conexión protegida con sesión de origen seguro</span></div><footer className="login-footer">¿Necesitas acceso? Contacta al administrador de tu cuenta.</footer>
    </div></section>
  </main>;
}

function AcceptInvitationPage() {
  const [token, setToken] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get('token') ?? '');
  const [password, setPassword] = useState(''); const [confirmPassword, setConfirmPassword] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [accepted, setAccepted] = useState(false);
  useEffect(() => {
    if (window.location.hash) window.history.replaceState(window.history.state, '', `${window.location.pathname}${window.location.search}`);
  }, []);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError('');
    if (!token) { setError('El enlace de invitación no contiene un token válido. Solicita una invitación nueva.'); return; }
    if (password.length < 12) { setError('La contraseña debe tener al menos 12 caracteres.'); return; }
    if (password !== confirmPassword) { setError('Las contraseñas no coinciden.'); return; }
    setBusy(true);
    try { await api.acceptInvitation(token, password); setToken(''); setPassword(''); setConfirmPassword(''); setAccepted(true); }
    catch (e) { setError(errText(e)); }
    finally { setBusy(false); }
  };
  return <main className="login-layout">
    <section className="login-aside"><div className="brand brand-light"><span className="brand-mark">M</span><span>markitdown</span></div><div className="aside-copy"><div className="eyebrow light">ACCESO SEGURO</div><h1>Tu espacio de trabajo<br/>está a un paso.</h1><p>Establece una contraseña para activar tu cuenta de MarkItDown.</p><div className="aside-note"><Icon name="shield" size={16}/> Invitación personal y protegida</div></div><div className="aside-ornament"><div className="orb orb-one"/><div className="orb orb-two"/><div className="orbit orbit-one"/><div className="orbit orbit-two"/><span className="orb-core">M</span></div><small className="aside-foot">MARKITDOWN · ACCESO A CUENTAS</small></section>
    <section className="login-main"><div className="login-form-wrap"><div className="login-mobile-brand brand"><span className="brand-mark">M</span><span>markitdown</span></div><div className="eyebrow">ACEPTAR INVITACIÓN</div><h2>{accepted ? 'Cuenta activada' : 'Crea tu contraseña'}</h2><p className="muted">{accepted ? 'Tu contraseña quedó configurada. Ya puedes iniciar sesión.' : 'Usa una contraseña segura de al menos 12 caracteres.'}</p>
      {error && <div className="alert alert-error" role="alert">{error}</div>}
      {accepted ? <><div className="alert alert-success" role="status">Invitación aceptada correctamente.</div><Link className="button button-primary button-full" to="/login">Ir a iniciar sesión <span aria-hidden="true">→</span></Link></> : <form className="form-stack login-form" onSubmit={submit}>
        {!token && <div className="alert alert-error" role="alert">Este enlace no tiene un token de invitación. Contacta a quien te invitó.</div>}
        <label>Contraseña<input autoComplete="new-password" type="password" minLength={12} required value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Mínimo 12 caracteres" /></label>
        <label>Confirmar contraseña<input autoComplete="new-password" type="password" minLength={12} required value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} placeholder="Vuelve a escribir tu contraseña" /></label>
        <button className="button button-primary button-full" disabled={busy || !token}>{busy ? <><span className="spinner spinner-light"/> Activando cuenta…</> : <>Establecer contraseña <span aria-hidden="true">→</span></>}</button>
      </form>}
      <div className="login-security"><Icon name="shield" size={16}/><span>Tu invitación se procesa de forma segura</span></div><footer className="login-footer"><Link to="/login">Volver al inicio de sesión</Link></footer>
    </div></section>
  </main>;
}

function Shell({ user, onLogout, mobileNav, setMobileNav }: { user: User; onLogout: () => void; mobileNav: boolean; setMobileNav: (value: boolean) => void }) {
  const isAdmin = adminRole(user);
  return <div className="app-shell">
    {mobileNav && <button className="nav-scrim" aria-label="Cerrar menú" onClick={() => setMobileNav(false)} />}
    <aside className={`sidebar ${mobileNav ? 'sidebar-open' : ''}`}>
      <Link to="/dashboard" className="brand"><span className="brand-mark">M</span><span>markitdown<span className="brand-admin">PANEL</span></span></Link>
      <div className="workspace-label">ESPACIO DE TRABAJO</div>
      <nav className="main-nav" aria-label="Navegación principal">
        <NavItem to="/dashboard" icon="grid" label="Resumen" onClick={() => setMobileNav(false)} />
        <NavItem to="/convert" icon="file" label="Convertir" onClick={() => setMobileNav(false)} />
        <NavItem to="/plans" icon="chart" label="Planes" onClick={() => setMobileNav(false)} />
        {isAdmin && <><div className="nav-section-label">ADMINISTRACIÓN</div><NavItem to="/users" icon="users" label="Usuarios" onClick={() => setMobileNav(false)} /><NavItem to="/reports/consumption" icon="chart" label="Consumo" onClick={() => setMobileNav(false)} /></>}
      </nav>
      <div className="sidebar-spacer" />
      <div className="sidebar-help"><div className="help-icon"><Icon name="shield" size={16}/></div><strong>Tu espacio, bajo control</strong><p>Consulta actividad, administra tu acceso y convierte con confianza.</p><Link to="/profile" onClick={() => setMobileNav(false)}>Ver mi perfil <span>→</span></Link></div>
      <div className="sidebar-bottom"><div className="sidebar-user"><Avatar label={displayName(user)} /><div className="sidebar-user-info"><strong>{displayName(user)}</strong><span>{user.email}</span></div><button className="icon-button logout-button" aria-label="Cerrar sesión" title="Cerrar sesión" onClick={onLogout}><Icon name="logout" size={17}/></button></div></div>
    </aside>
    <div className="main-column"><header className="topbar"><button className="mobile-menu icon-button" aria-label="Abrir menú" onClick={() => setMobileNav(!mobileNav)}><span className="hamburger"/></button><div className="breadcrumbs"><span>Workspace</span><span className="crumb-slash">/</span><strong>MarkItDown</strong></div><div className="topbar-right"><span className="system-status"><i/> Sistema activo</span><Link className="top-profile" to="/profile" aria-label="Perfil de usuario"><Avatar label={displayName(user)} small /></Link></div></header><main className="page-content"><Outlet context={{ user }} /></main><footer className="page-footer"><span>MarkItDown <span className="footer-dot">·</span> Espacio seguro</span><span>Zona horaria: {localZone}</span></footer></div>
  </div>;
}

function NavItem({ to, icon, label, onClick }: { to: string; icon: string; label: string; onClick: () => void }) {
  return <NavLink to={to} onClick={onClick} className={({ isActive }) => `nav-item ${isActive ? 'nav-item-active' : ''}`}><Icon name={icon} size={18}/><span>{label}</span>{label === 'Consumo' && <span className="nav-new">LIVE</span>}</NavLink>;
}
function Avatar({ label, small = false }: { label: string; small?: boolean }) { return <span className={`avatar ${small ? 'avatar-small' : ''}`} aria-hidden="true">{label.slice(0, 1).toUpperCase()}</span>; }
function PageHeader({ eyebrow, title, description, action }: { eyebrow?: string; title: string; description?: string; action?: ReactNode }) {
  return <div className="page-heading"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1>{description && <p>{description}</p>}</div>{action && <div className="heading-action">{action}</div>}</div>;
}
function Loading() { return <div className="state-card"><span className="spinner"/><span>Cargando información…</span></div>; }
function ErrorState({ error, retry }: { error: string; retry?: () => void }) { return <div className="alert alert-error" role="alert"><strong>No se pudo cargar.</strong> {error}{retry && <button className="text-button" onClick={retry}>Reintentar</button>}</div>; }
function Empty({ title, description }: { title: string; description: string }) { return <div className="empty-state"><div className="empty-icon"><Icon name="file" size={22}/></div><strong>{title}</strong><p>{description}</p></div>; }
function Metric({ label, value: metricValue, detail, icon, accent = '' }: { label: string; value: unknown; detail?: ReactNode; icon: string; accent?: string }) { const safeValue: ReactNode = typeof metricValue === 'string' || typeof metricValue === 'number' || isValidElement(metricValue) ? metricValue as ReactNode : '—'; return <article className="metric-card"><div className={`metric-icon ${accent}`}><Icon name={icon} size={19}/></div><div className="metric-label">{label}</div><div className="metric-value">{safeValue}</div>{detail && <div className="metric-detail">{detail}</div>}</article>; }

function Dashboard({ user }: { user: User }) {
  const admin = adminRole(user); const [data, setData] = useState<Record<string, unknown> | null>(null); const [error, setError] = useState(''); const [loading, setLoading] = useState(admin);
  const load = useCallback(() => { if (!admin) return; setLoading(true); api.overview().then(setData).catch((e) => setError(errText(e))).finally(() => setLoading(false)); }, [admin]);
  useEffect(() => { load(); }, [load]);
  const overview = data ?? {};
  const count = value(overview, 'conversionCount', 'conversion_count', 'conversionsThisMonth', 'conversions_this_month');
  const users = value(overview, 'userCount', 'user_count', 'totalUsers', 'total_users');
  const bytes = value(overview, 'inputBytes', 'input_bytes');
  const recent = records<Record<string, unknown>>(value(overview, 'recentActivity', 'recent_activity', 'items'));
  return <>
    <PageHeader eyebrow={admin ? 'VISTA GENERAL' : 'TU ESPACIO'} title={`Hola, ${displayName(user)}`} description={admin ? 'Un vistazo a la actividad y el estado de tu plataforma.' : 'Aquí tienes un resumen de tu actividad reciente.'} action={<Link className="button button-primary" to="/convert"><Icon name="plus" size={17}/> Nueva conversión</Link>} />
    {!admin ? <ClientDashboard user={user}/> : loading ? <Loading/> : error ? <ErrorState error={error} retry={load}/> : <>
      <section className="metric-grid" aria-label="Métricas principales"><Metric label="Conversiones este mes" value={metricText(count)} detail="Mes natural · zona Mazatlán" icon="file" accent="metric-blue"/><Metric label="Usuarios activos" value={metricText(users)} detail="Cuentas registradas" icon="users" accent="metric-violet"/><Metric label="Datos procesados" value={typeof bytes === 'number' ? formatBytes(bytes) : '—'} detail="Acumulado disponible" icon="chart" accent="metric-green"/><Metric label="Servicio" value={<span className="status-value"><i/> Operativo</span>} detail="API y cola de trabajo" icon="shield" accent="metric-amber"/></section>
      <section className="dashboard-grid"><div className="panel activity-panel"><div className="panel-header"><div><h2>Actividad reciente</h2><p>Últimos eventos de la plataforma</p></div><Link className="inline-link" to="/reports/consumption">Ver informe <Icon name="arrow" size={14}/></Link></div>{recent.length ? <div className="table-wrap"><table><thead><tr><th>Actividad</th><th>Cuenta</th><th>Estado</th><th>Fecha</th></tr></thead><tbody>{recent.slice(0, 6).map((item, i) => <tr key={String(item.id ?? i)}><td><span className="row-title">{String(value(item, 'type', 'event', 'action') ?? 'Conversión')}</span><small>{String(value(item, 'format', 'inputType') ?? 'Documento')}</small></td><td>{String(value(item, 'email', 'account') ?? '—')}</td><td><StatusBadge status={String(item.status ?? 'completed')}/></td><td>{dateLabel(String(value(item, 'createdAt', 'created_at') ?? ''))}</td></tr>)}</tbody></table></div> : <Empty title="Sin actividad todavía" description="Los eventos aparecerán aquí cuando tus usuarios comiencen a convertir documentos."/>}</div>
        <aside className="panel quick-panel"><div className="panel-header"><div><h2>Accesos rápidos</h2><p>Administra tu instancia</p></div></div><Link className="quick-link" to="/users"><span className="quick-icon quick-blue"><Icon name="users"/></span><span><strong>Gestionar usuarios</strong><small>Invita y administra cuentas</small></span><Icon name="arrow" size={15}/></Link><Link className="quick-link" to="/reports/consumption"><span className="quick-icon quick-violet"><Icon name="chart"/></span><span><strong>Consumo y actividad</strong><small>Consulta métricas y exporta</small></span><Icon name="arrow" size={15}/></Link><div className="notice-card"><div className="notice-badge">i</div><div><strong>Planes y límites</strong><p>Los límites configurados se consultan en el catálogo de planes.</p><Link to="/users">Ver usuarios y planes <span>→</span></Link></div></div></aside>
      </section>
    </>}
  </>;
}

function ClientDashboard({ user }: { user: User }) {
  const [usage, setUsage] = useState<unknown>(null); const [error, setError] = useState(''); const [loading, setLoading] = useState(true);
  useEffect(() => { api.usage('limit=5').then(setUsage).catch((e) => setError(errText(e))).finally(() => setLoading(false)); }, []);
  const rows = records<Record<string, unknown>>(usage);
  const plan = typeof user.plan === 'string' ? user.plan : user.plan?.name || 'Free';
  const isFree = plan.toLowerCase() === 'free';
  const monthlyCap = isFree ? 300 : userPlanLimit(user, 'monthly_conversion_limit', 'conversions_per_month', 'monthly_conversions');
  const fileCap = isFree ? '10 MiB' : userFileLimit(user)?.label ?? 'Según catálogo';
  return <><section className="metric-grid client-metrics"><Metric label="Plan actual" value={plan} detail={plan.toLowerCase() === 'business' ? 'Cuenta individual · sin organizaciones en v1' : 'Administrado por tu cuenta'} icon="shield" accent="metric-violet"/><Metric label="Límite mensual" value={monthlyCap ?? 'Según catálogo'} detail="Mes natural · reinicio America/Mazatlan" icon="file" accent="metric-blue"/><Metric label="Tamaño máximo por archivo" value={fileCap} detail="El límite global de entrada es 100 MiB" icon="chart" accent="metric-green"/><Metric label="Unidad de página" value="1 por archivo" detail="Si page_count no es fiable" icon="clock" accent="metric-amber"/></section><div className="plan-intro dashboard-policy"><span className="info-mark">i</span><p>Solo se descuenta una conversión y páginas cuando el trabajo tiene éxito. En fallos no se descuentan conversiones ni páginas; los MB efectivamente procesados y las solicitudes por minuto sí se contabilizan. Si hay paginación fiable, se registra el page_count real; para imágenes y formatos sin paginación fiable se usa 1 unidad por archivo.</p></div><section className="panel activity-panel"><div className="panel-header"><div><h2>Tus conversiones</h2><p>Tu actividad de conversión más reciente</p></div><Link className="button button-secondary button-small" to="/convert">Convertir archivo</Link></div>{loading ? <Loading/> : error ? <ErrorState error={error}/> : rows.length ? <div className="table-wrap"><table><thead><tr><th>Conversión</th><th>Entrada</th><th>Estado</th><th>Fecha</th></tr></thead><tbody>{rows.map((row, i) => <tr key={String(row.id ?? i)}><td><span className="row-title">{String(value(row, 'id', 'conversion_id') ?? 'Conversión')}</span></td><td>{String(value(row, 'inputType', 'input_type', 'extension') ?? '—')}</td><td><StatusBadge status={String(value(row, 'status', 'outcome') ?? '—')}/></td><td>{dateLabel(String(value(row, 'createdAt', 'created_at', 'timestamp') ?? ''))}</td></tr>)}</tbody></table></div> : <Empty title="Tu actividad aparecerá aquí" description="Inicia una conversión y podrás ver su estado en este espacio."/>}</section></>;
}

function publicPlanFeatures(plan: Plan): string[] {
  if (Array.isArray(plan.features)) return plan.features.filter((feature): feature is string => typeof feature === 'string');
  if (typeof plan.features_json === 'string') {
    try { const parsed: unknown = JSON.parse(plan.features_json); return Array.isArray(parsed) ? parsed.filter((feature): feature is string => typeof feature === 'string') : []; }
    catch { return []; }
  }
  return [];
}

function publicPlanLimits(plan: Plan): [string, unknown][] {
  const entries = configuredPlanLimits(plan);
  if (plan.name.trim().toLowerCase() !== 'free') return entries;
  const keys = new Set(entries.map(([key]) => key.toLowerCase()));
  const hasMonthlyConversions = [...keys].some((key) => ['max_conversions_month', 'monthly_conversion_limit', 'conversions_per_month', 'monthly_conversions'].includes(key));
  const hasFileBytes = [...keys].some((key) => ['max_file_bytes', 'max_file_size_bytes', 'max_input_bytes', 'max_file_mb', 'max_file_size_mb', 'max_file_mib'].includes(key));
  if (!hasMonthlyConversions) entries.push(['max_conversions_month', 300]);
  if (!hasFileBytes) entries.push(['max_file_mib', 10]);
  return entries;
}

function publicPlanLimitLabel(key: string) {
  const labels: Record<string, string> = {
    max_conversions_month: 'Conversiones por mes natural', monthly_conversion_limit: 'Conversiones por mes natural', conversions_per_month: 'Conversiones por mes natural',
    max_input_bytes_month: 'Entrada por mes', max_pages_month: 'Unidades de página por mes', requests_per_minute: 'Solicitudes por minuto',
    max_file_bytes: 'Tamaño máximo por archivo', max_file_size_bytes: 'Tamaño máximo por archivo', max_input_bytes: 'Tamaño máximo por archivo',
    max_file_mb: 'Tamaño máximo por archivo', max_file_size_mb: 'Tamaño máximo por archivo', max_file_mib: 'Tamaño máximo por archivo',
    max_batch_files: 'Archivos por lote', batch_max_files: 'Archivos por lote', max_ocr_units_month: 'Unidades OCR por mes', ocr_enabled: 'OCR por plan',
  };
  return labels[key] ?? key.replaceAll('_', ' ');
}

function publicPlanLimitValue(key: string, raw: unknown): string {
  if (key === 'max_file_mib') return `${String(raw)} MiB`;
  if (typeof raw === 'number' && ['max_file_bytes', 'max_file_size_bytes', 'max_input_bytes'].includes(key)) return `${(raw / (1024 * 1024)).toLocaleString(undefined, { maximumFractionDigits: 2 })} MiB`;
  if (typeof raw === 'number' && key === 'max_input_bytes_month') return `${(raw / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 2 })} MB`;
  if (typeof raw === 'boolean') return raw ? 'Habilitado' : 'Deshabilitado';
  return typeof raw === 'object' ? JSON.stringify(raw) : String(raw);
}

function PublicPlansPage() {
  const [plans, setPlans] = useState<Plan[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const load = useCallback(() => { setLoading(true); setError(''); api.publicPlans().then((data) => setPlans(records<Plan>(data).filter(planIsActive))).catch((e) => setError(errText(e))).finally(() => setLoading(false)); }, []);
  useEffect(() => { load(); }, [load]);
  return <><PageHeader eyebrow="CATÁLOGO" title="Planes y funciones" description="Consulta los límites y prestaciones disponibles en tu cuenta. Los precios son informativos; no hay compra desde esta página." />
    {error ? <ErrorState error={error} retry={load}/> : loading ? <Loading/> : plans.length === 0 ? <div className="panel"><Empty title="No hay planes activos" description="Los planes se habilitarán cuando termine el benchmark y se complete la matriz de cuotas."/></div> : <div className="public-plan-grid">{plans.map((plan) => {
      const limits = publicPlanLimits(plan); const features = publicPlanFeatures(plan);
      const price = plan.price ?? plan.display_price;
      const interval = plan.interval ?? plan.billing_period;
      return <article className="panel public-plan-card" key={plan.id}>
        <div className="public-plan-top"><span className="plan-icon"><Icon name="file" size={19}/></span><span className="status-badge badge-good"><i/> Disponible</span></div>
        <h2>{plan.name}</h2><p className="public-plan-description">{plan.description || (plan.name.toLowerCase() === 'business' ? 'Cuenta individual en la primera versión; no incluye organizaciones.' : 'Detalles del plan disponibles en el catálogo.')}</p>
        <div className="public-plan-price"><strong>{typeof price === 'number' || typeof price === 'string' ? `${typeof plan.currency === 'string' ? `${plan.currency} ` : ''}${price}` : 'Precio no publicado'}</strong><span>{typeof interval === 'string' ? interval : 'Intervalo no publicado'} · solo informativo</span></div>
        <section className="public-plan-section"><h3>Límites y cuotas</h3>{limits.length ? <dl>{limits.map(([key, raw]) => <div className="public-limit-row" key={key}><dt>{publicPlanLimitLabel(key)}</dt><dd>{publicPlanLimitValue(key, raw)}</dd></div>)}</dl> : <p className="muted">Límites pendientes de configuración en el catálogo.</p>}</section>
        <section className="public-plan-section"><h3>Funciones disponibles</h3>{features.length ? <ul className="public-feature-list">{features.map((feature) => <li key={feature}><span aria-hidden="true">✓</span>{feature}</li>)}</ul> : <p className="muted">No hay funciones adicionales configuradas.</p>}</section>
        {plan.name.toLowerCase() === 'business' && <p className="public-plan-note">Business es una cuenta individual en v1; no hay soporte para organizaciones.</p>}
      </article>;
    })}</div>}
    <div className="catalog-footnote"><Icon name="shield" size={15}/><span>La disponibilidad y aplicación de límites se valida en el API. Esta vista no inicia compras ni pagos.</span></div>
  </>;
}

function StatusBadge({ status }: { status: string }) { const normalized = status.toLowerCase(); const good = ['succeeded', 'success', 'completed', 'active', 'verified'].includes(normalized); const bad = ['failed', 'error', 'cancelled', 'expired', 'inactive'].includes(normalized); const labels: Record<string, string> = { succeeded: 'Completada', success: 'Completada', completed: 'Completada', queued: 'En cola', running: 'Procesando', failed: 'Fallida', expired: 'Expirada', cancelled: 'Cancelada', active: 'Activo', inactive: 'Inactivo', verified: 'Verificado' }; return <span className={`status-badge ${good ? 'badge-good' : bad ? 'badge-bad' : 'badge-wait'}`}><i/>{labels[normalized] ?? status}</span>; }
function formatBytes(bytes: number) { if (bytes < 1_000_000) return `${(bytes / 1_000).toFixed(0)} KB`; return `${(bytes / 1_000_000).toFixed(1)} MB`; }
function metricText(raw: unknown): ReactNode { return typeof raw === 'string' || typeof raw === 'number' ? raw : typeof raw === 'boolean' ? (raw ? 'Sí' : 'No') : '—'; }
function csvText(data: unknown): string {
  if (typeof data === 'string') return data;
  const rows = records<Record<string, unknown>>(data);
  if (!rows.length) return '';
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  const cell = (value: unknown) => `"${String(value === null || value === undefined ? '' : typeof value === 'object' ? JSON.stringify(value) : value).replaceAll('"', '""')}"`;
  return [columns.map(cell).join(','), ...rows.map((row) => columns.map((key) => cell(row[key])).join(','))].join('\r\n');
}

function ConvertPage({ user }: { user: User }) {
  const [mode, setMode] = useState<'file' | 'url'>('file'); const [file, setFile] = useState<File | null>(null); const [url, setUrl] = useState('');
  const [ocr, setOcr] = useState(false); const [consent, setConsent] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [job, setJob] = useState<Conversion | null>(null); const [markdown, setMarkdown] = useState<string | null>(null); const [resultError, setResultError] = useState(''); const [key, setKey] = useState(() => sessionStorage.getItem('markitdown-api-key') ?? '');
  const freePlan = (typeof user.plan === 'string' ? user.plan : user.plan?.name ?? '').toLowerCase() === 'free';
  const catalogFileLimit = userFileLimit(user);
  const maxBytes = freePlan ? 10 * 1024 * 1024 : Math.min(catalogFileLimit?.bytes ?? 100 * 1024 * 1024, 100 * 1024 * 1024);
  const displayedMax = freePlan ? '10 MiB' : catalogFileLimit ? (catalogFileLimit.bytes > 100 * 1024 * 1024 ? '100 MiB · máximo global' : catalogFileLimit.label) : 'según catálogo · máximo global 100 MiB';
  const hasOcrCapability = userPlanHasOcr(user);
  const jobStatus = (job?.status ?? '').toLowerCase(); const working = ['queued', 'running', 'processing', 'pending'].includes(jobStatus);

  useEffect(() => {
    if (!job || !working || !key) return;
    let cancelled = false; let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const fresh = await api.conversion(job.id, key);
        if (cancelled) return;
        const status = fresh.status.toLowerCase();
        if (['succeeded', 'completed', 'success'].includes(status)) {
          // Deliberately retrieve once. The response is held only in React memory.
          try {
            const result = await api.result(fresh.id, key);
            if (cancelled) return;
            const text = typeof result === 'string' ? result : result && typeof result === 'object' ? String(value(result as Record<string, unknown>, 'markdown', 'content', 'result') ?? '') : '';
            setJob(fresh); setMarkdown(text);
          } catch (e) {
            if (!cancelled) { setJob(fresh); setResultError(`El resultado no se pudo recibir completo. No se volverá a solicitar automáticamente. ${errText(e)}`); }
          }
        } else {
          setJob(fresh);
          if (!['failed', 'expired', 'cancelled'].includes(status)) timer = setTimeout(poll, 1800);
        }
      } catch (e) { if (!cancelled) { setError(errText(e)); timer = setTimeout(poll, 3000); } }
    };
    timer = setTimeout(poll, 1100);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [job?.id, job?.status, working, key]);

  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(''); setResultError(''); setMarkdown(null); setJob(null);
    if (!key.trim()) { setError('Primero agrega una clave API en Perfil para iniciar una conversión.'); return; }
    if (mode === 'file' && !file) { setError('Selecciona un archivo para continuar.'); return; }
    if (file && file.size > maxBytes) { setError(`El archivo supera el límite disponible de ${displayedMax}.`); return; }
    if (ocr && !consent) { setError('Confirma el consentimiento para procesar imágenes del documento con el proveedor OCR.'); return; }
    setBusy(true);
    try { const result = mode === 'file' ? await api.upload(file!, ocr, key) : await api.fromUrl(url.trim(), ocr, key); setJob(result); }
    catch (e) { setError(errText(e)); }
    finally { setBusy(false); }
  };
  const copy = async () => { if (markdown === null) return; try { await navigator.clipboard.writeText(markdown); setError(''); } catch { setError('No fue posible copiar. Verifica los permisos del navegador.'); } };
  const download = () => { if (markdown === null) return; const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' }); const href = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = href; a.download = `${job?.id || 'markitdown'}.md`; a.click(); URL.revokeObjectURL(href); };

  return <><PageHeader eyebrow="CONVERSIÓN" title="Convertir a Markdown" description="Sube un documento o indica una URL pública. Procesamos tu solicitud en segundo plano." />
    <div className="convert-layout"><section className="panel convert-panel"><div className="panel-header"><div><h2>Nueva conversión</h2><p>Solo se procesa un archivo por solicitud.</p></div><span className="secure-label"><Icon name="shield" size={15}/> Privado</span></div>
      <div className="segmented-control" role="tablist" aria-label="Tipo de origen"><button role="tab" aria-selected={mode === 'file'} className={mode === 'file' ? 'segment-active' : ''} onClick={() => setMode('file')}><Icon name="file" size={16}/> Archivo</button><button role="tab" aria-selected={mode === 'url'} className={mode === 'url' ? 'segment-active' : ''} onClick={() => setMode('url')}><span className="url-glyph">↗</span> Desde URL</button></div>
      <form onSubmit={submit} className="form-stack conversion-form">
        {mode === 'file' ? <label className="upload-zone"><input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Selecciona un documento"/><span className="upload-icon"><Icon name="file" size={22}/></span><strong>{file?.name ?? 'Arrastra tu archivo o haz clic para buscar'}</strong><small>{file ? `${formatBytes(file.size)} · listo para convertir` : `PDF, Word, Excel y más · máximo ${displayedMax}`}</small><span className="button button-secondary button-small">Seleccionar archivo</span></label> : <label>URL del documento<div className="input-with-icon"><span>↗</span><input type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://ejemplo.com/documento.pdf" /></div><small className="field-hint">Solo URLs HTTP/HTTPS públicas. No se admiten destinos internos o privados.</small></label>}
        {hasOcrCapability && <div className="ocr-box"><label className="check-row"><input type="checkbox" checked={ocr} onChange={(e) => { setOcr(e.target.checked); if (!e.target.checked) setConsent(false); }}/><span><strong>Usar OCR en las imágenes del documento</strong><small>Habilitado por tu plan. Se envían imágenes al proveedor externo configurado.</small></span></label>{ocr && <label className="check-row consent-row"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)}/><span><strong>Consiento el procesamiento externo</strong><small>La unidad OCR se determina por páginas e imágenes efectivamente enviadas.</small></span></label>}</div>}
        {error && <div className="alert alert-error" role="alert">{error}</div>}
        <button className="button button-primary button-full" disabled={busy || working}>{busy ? <><span className="spinner spinner-light"/> Enviando…</> : working ? 'Conversión en curso…' : <>Iniciar conversión <span>→</span></>}</button>
      </form><div className="conversion-footnote"><Icon name="clock" size={15}/><span>Los resultados se conservan hasta su descarga o por un máximo de 24 horas.</span></div>
    </section>
      <aside className="convert-side"><div className="panel limit-panel"><div className="panel-header"><div><h2>Límites de entrada</h2><p>Según tu plan activo</p></div></div><div className="limit-row"><span>Plan actual</span><strong>{typeof user.plan === 'string' ? user.plan : user.plan?.name ?? '—'}</strong></div><div className="limit-row"><span>Tamaño máximo</span><strong>{displayedMax}</strong></div><div className="limit-row"><span>Lote por solicitud</span><strong>1 archivo · configurable</strong></div><div className="limit-row"><span>Unidad de página</span><strong>page_count real si fiable</strong></div><p className="muted small-note">Batch máximo inicia en 1 y se configura por API. El máximo global es 100 MiB; los valores de planes de pago dependen del catálogo. La aceptación del tamaño no garantiza que un formato complejo pueda procesarse.</p>{!hasOcrCapability && <p className="muted small-note">OCR no está habilitado en el plan informado por el API.</p>}<Link to="/profile" className="inline-link">Administrar clave API <Icon name="arrow" size={14}/></Link></div>
        {job && <div className="panel job-panel" aria-live="polite"><div className="panel-header"><div><h2>Estado de la solicitud</h2><p>ID <code>{job.id}</code></p></div><StatusBadge status={job.status}/></div>{working ? <><div className="progress-track"><span className={jobStatus === 'queued' ? 'progress-queued' : ''}/></div><p className="muted">{jobStatus === 'queued' ? 'En espera de un espacio de procesamiento…' : 'El documento se está convirtiendo…'}</p></> : ['failed', 'expired', 'cancelled'].includes(jobStatus) ? <div className="job-error"><strong>La conversión no pudo completarse.</strong><span>{job.errorCode ?? job.error_code ?? 'Revisa el estado e inténtalo de nuevo.'}</span></div> : markdown !== null ? <div className="result-tools"><div className="result-summary"><span className="result-check">✓</span><span><strong>Markdown listo</strong><small>Resultado recuperado. Esta respuesta no se vuelve a solicitar.</small></span></div><div className="result-actions"><button className="button button-secondary" onClick={copy}><Icon name="copy" size={16}/> Copiar</button><button className="button button-primary" onClick={download}><Icon name="download" size={16}/> Descargar .md</button></div><pre className="markdown-preview" aria-label="Vista previa del Markdown">{markdown.slice(0, 12000)}{markdown.length > 12000 ? '\n\n… Vista previa truncada' : ''}</pre></div> : resultError ? <div className="job-error"><strong>Conversión completada, descarga interrumpida.</strong><span>{resultError}</span></div> : <div className="state-inline"><span className="spinner"/> Recuperando resultado…</div>}</div>}
      </aside>
    </div>
  </>;
}

function UsersPage() {
  const [tab, setTab] = useState<'users' | 'plans'>('users'); const [users, setUsers] = useState<User[]>([]); const [plans, setPlans] = useState<Plan[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [query, setQuery] = useState(''); const [showCreate, setShowCreate] = useState(false); const [notice, setNotice] = useState('');
  const load = useCallback(async () => { setLoading(true); setError(''); try { const [u, p] = await Promise.all([api.users(), api.plans()]); setUsers(records<User>(u)); setPlans(records<Plan>(p)); } catch (e) { setError(errText(e)); } finally { setLoading(false); } }, []);
  useEffect(() => { load(); }, [load]);
  const filtered = users.filter((item) => `${displayName(item)} ${item.email}`.toLowerCase().includes(query.toLowerCase()));
  return <><PageHeader eyebrow="ADMINISTRACIÓN" title="Usuarios y planes" description="Administra las cuentas y consulta el catálogo configurado en el API." action={tab === 'users' ? <button className="button button-primary" onClick={() => setShowCreate(true)}><Icon name="plus" size={17}/> Añadir usuario</button> : undefined}/>
    <div className="tabs" role="tablist"><button className={tab === 'users' ? 'tab-active' : ''} onClick={() => setTab('users')} role="tab" aria-selected={tab === 'users'}>Usuarios <span>{users.length}</span></button><button className={tab === 'plans' ? 'tab-active' : ''} onClick={() => setTab('plans')} role="tab" aria-selected={tab === 'plans'}>Planes <span>{plans.length}</span></button></div>
    {notice && <div className="alert alert-success" role="status">{notice}<button className="alert-close" onClick={() => setNotice('')} aria-label="Cerrar">×</button></div>}{error ? <ErrorState error={error} retry={load}/> : loading ? <Loading/> : tab === 'users' ? <section className="panel table-panel"><div className="table-toolbar"><div className="search-box"><Icon name="search" size={17}/><input aria-label="Buscar usuario" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Buscar por nombre o correo…"/></div><span className="muted">{filtered.length} cuentas</span></div>{filtered.length ? <div className="table-wrap"><table><thead><tr><th>Usuario</th><th>Plan</th><th>Rol</th><th>Estado</th><th>Creado</th><th></th></tr></thead><tbody>{filtered.map((item) => <tr key={item.id}><td><Link className="user-cell" to={`/users/${encodeURIComponent(item.id)}`}><Avatar label={displayName(item)} small/><span><strong>{displayName(item)}</strong><small>{item.email}</small></span></Link></td><td>{typeof item.plan === 'string' ? item.plan : item.plan?.name ?? '—'}</td><td><span className="role-label">{item.role ?? item.roles?.[0] ?? 'Cliente'}</span></td><td><StatusBadge status={item.status ?? (item.is_active === false ? 'inactive' : ((item.emailVerified ?? item.email_verified) ? 'active' : 'pending'))}/></td><td>{dateLabel(item.createdAt ?? item.created_at)}</td><td><Link className="row-action" to={`/users/${encodeURIComponent(item.id)}`} aria-label={`Ver usuario ${item.email}`}>→</Link></td></tr>)}</tbody></table></div> : <Empty title="No hay usuarios que coincidan" description={query ? 'Prueba con otro término de búsqueda.' : 'Crea una cuenta para comenzar.'}/>}</section> : <PlansPanel plans={plans} onChanged={async (message) => { setNotice(message); await load(); }}/>}
    {showCreate && <CreateUserDialog plans={plans} onClose={() => setShowCreate(false)} onCreated={async () => { setShowCreate(false); setNotice('Usuario creado correctamente.'); await load(); }}/>}
  </>;
}

function PlansPanel({ plans, onChanged }: { plans: Plan[]; onChanged: (message: string) => Promise<void> }) {
  const [selected, setSelected] = useState<Plan | null>(null); const [createMode, setCreateMode] = useState(false); const [saving, setSaving] = useState(false); const [error, setError] = useState(''); const [draft, setDraft] = useState('');
  const blankPlan = { name: '', description: '', active: false, max_conversions_month: null, max_input_bytes_month: null, max_pages_month: null, requests_per_minute: null, max_file_bytes: null, max_batch_files: 1, ocr_enabled: false, max_ocr_units_month: null, features: [] };
  const closeEditor = () => { setSelected(null); setCreateMode(false); setError(''); setDraft(''); };
  const savePlan = async () => {
    setError('');
    try {
      const data = JSON.parse(draft) as Record<string, unknown>;
      if (typeof data.name !== 'string' || !data.name.trim()) { setError('El plan debe tener un nombre.'); return; }
      if (quotaValue(data, 'max_batch_files') !== undefined) data.max_batch_files = quotaValue(data, 'max_batch_files');
      const activating = data.active === true || data.is_active === true;
      const matrix = quotaMatrix(data);
      if (activating && !matrix.complete) { setError(`Completa la matriz de cuotas antes de activar el plan: ${matrix.missing.join(', ')}.`); return; }
      delete data.id;
      setSaving(true);
      if (createMode) await api.createPlan(data);
      else if (selected) await api.updatePlan(selected.id, data);
      closeEditor();
      await onChanged(createMode ? 'Plan creado.' : 'Plan actualizado.');
    } catch (e) { setError(e instanceof SyntaxError ? 'El JSON no es válido.' : errText(e)); }
    finally { setSaving(false); }
  };
  let editorPlan: Record<string, unknown> = {};
  try { editorPlan = JSON.parse(draft) as Record<string, unknown>; } catch { /* Keep the editor open while the JSON is incomplete. */ }
  if (editorPlan.max_batch_files === undefined && quotaValue(editorPlan, 'max_batch_files') !== undefined) editorPlan.max_batch_files = quotaValue(editorPlan, 'max_batch_files');
  const editorMatrix = selected || createMode ? quotaMatrix(editorPlan) : null;
  return <>
    <div className="plan-intro"><span className="info-mark">i</span><p>Los valores se cargan del catálogo editable del API. Free: 300 conversiones por mes natural y 10 MiB por archivo. Los límites MB/páginas/RPM de planes de pago y OCR están pendientes de benchmark. Business en v1 es una cuenta individual, sin organizaciones. Precios solo informativos; no hay pagos.</p><button className="button button-primary button-small" onClick={() => {setSelected(null); setCreateMode(true); setDraft(JSON.stringify(blankPlan, null, 2)); setError('');}}><Icon name="plus" size={15}/> Nuevo plan</button></div>
    {plans.length ? <div className="plan-grid">{plans.map((plan) => { const limits = configuredPlanLimits(plan); return <article className="panel plan-card" key={plan.id}><div className="plan-card-top"><span className="plan-icon"><Icon name="file" size={18}/></span><StatusBadge status={planIsActive(plan) ? 'active' : 'inactive'}/></div><h2>{plan.name}</h2><p className="plan-description">{plan.description || (plan.name.toLowerCase() === 'business' ? 'Cuenta individual; las organizaciones no están incluidas en v1.' : 'Sin descripción configurada.')}</p><div className="plan-price">{plan.price === undefined || plan.price === null ? '—' : `${plan.currency ?? ''} ${plan.price}`}<small>{plan.interval ? ` / ${plan.interval}` : 'Precio informativo · sin pago'}</small></div><div className="plan-limits"><strong>Límites y capacidades del catálogo</strong>{limits.length ? limits.map(([key, val]) => <div className="limit-row" key={key}><span>{key.replaceAll('_', ' ')}</span><b>{typeof val === 'object' ? JSON.stringify(val) : String(val)}</b></div>) : <p className="muted">Sin límites configurados. Los valores de planes de pago/OCR quedan pendientes de benchmark.</p>}{plan.features?.length ? <div className="feature-list">{plan.features.map((f) => <span key={f}>✓ {f}</span>)}</div> : null}</div><button className="button button-secondary button-full button-small" onClick={() => {setSelected(plan); setCreateMode(false); setDraft(JSON.stringify(plan, null, 2)); setError('');}}>Editar configuración</button></article>;})}</div> : <Empty title="El catálogo no contiene planes" description="Cuando el API publique sus planes, se mostrarán aquí sin asumir valores por defecto."/>}
    {(selected || createMode) && <div className="modal-backdrop" role="presentation"><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="edit-plan-title"><div className="modal-heading"><div><div className="eyebrow">CATÁLOGO API</div><h2 id="edit-plan-title">{createMode ? 'Crear plan' : `Editar plan: ${selected?.name}`}</h2></div><button className="icon-button" onClick={closeEditor} aria-label="Cerrar">×</button></div><p className="muted">Completa los límites en el JSON antes de activar. No uses valores estimados: la matriz debe estar aprobada y completa; el API también validará la activación.</p><div className="quota-checklist" aria-live="polite"><strong>Matriz de cuotas para activar</strong>{quotaFields.map(([key, label]) => { const raw = editorPlan[key] ?? (editorPlan.limits as Record<string, unknown> | undefined)?.[key]; const valid = raw !== null && raw !== undefined && raw !== '' && Number.isFinite(Number(raw)) && Number(raw) >= 0 && (key !== 'max_file_bytes' || Number(raw) <= 100 * 1024 * 1024) && (key !== 'max_batch_files' || Number(raw) >= 1) && (key !== 'requests_per_minute' || Number(raw) > 0); return <div className="quota-check-row" key={key}><span className={valid ? 'quota-check-ok' : 'quota-check-missing'}>{valid ? '✓' : '○'}</span><span>{label}</span><code>{valid ? String(raw) : key}</code></div>;})}<div className="quota-check-row"><span className={editorMatrix?.ocrEnabled === false ? 'quota-check-ok' : editorMatrix?.ocrEnabled === true && editorMatrix.missing.includes('max_ocr_units_month') === false ? 'quota-check-ok' : 'quota-check-missing'}>{editorMatrix?.ocrEnabled === false || editorMatrix?.ocrEnabled === true && !editorMatrix.missing.includes('max_ocr_units_month') ? '✓' : '○'}</span><span>OCR (habilitado por plan)</span><code>{editorMatrix?.ocrEnabled === true ? `max_ocr_units_month: ${String(editorPlan.max_ocr_units_month ?? (editorPlan.limits as Record<string, unknown> | undefined)?.max_ocr_units_month ?? '—')}` : editorMatrix?.ocrEnabled === false ? 'Deshabilitado' : 'ocr_enabled'}</code></div></div><label>Datos del plan (JSON)<textarea className="code-input" value={draft} onChange={(event) => setDraft(event.target.value)} id="plan-json" rows={12}/></label>{error && <div className="alert alert-error" role="alert">{error}</div>}<div className="modal-actions"><button className="button button-secondary" onClick={closeEditor}>Cancelar</button><button className="button button-primary" disabled={saving} onClick={savePlan}>{saving ? 'Guardando…' : createMode ? 'Crear plan' : 'Guardar cambios'}</button></div></section></div>}
  </>;
}

function CreateUserDialog({ plans, onClose, onCreated }: { plans: Plan[]; onClose: () => void; onCreated: () => Promise<void> }) {
  const activePlans = plans.filter(planIsActive);
  const [email, setEmail] = useState(''); const [name, setName] = useState(''); const [planId, setPlanId] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const submit = async (event: FormEvent) => { event.preventDefault(); setError(''); if (!planId || !activePlans.some((plan) => plan.id === planId)) { setError('Selecciona un plan activo para enviar la invitación.'); return; } setBusy(true); try { await api.createUser({ email: email.trim(), display_name: name.trim() || undefined, planId }); await onCreated(); } catch (e) {setError(errText(e));} finally {setBusy(false);} };
  return <div className="modal-backdrop" role="presentation"><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="create-user-title"><div className="modal-heading"><div><div className="eyebrow">NUEVA CUENTA</div><h2 id="create-user-title">Añadir usuario</h2></div><button className="icon-button" onClick={onClose} aria-label="Cerrar">×</button></div><p className="muted">La invitación requiere asignar un plan activo. El API enviará los pasos de acceso y verificación.</p>{activePlans.length === 0 && <div className="alert alert-error" role="status"><strong>No hay planes activos.</strong> Completa la matriz de cuotas y activa un plan antes de invitar usuarios.</div>}<form className="form-stack" onSubmit={submit}><label>Nombre<input value={name} onChange={(e) => setName(e.target.value)} placeholder="Nombre del usuario"/></label><label>Correo electrónico<input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="usuario@empresa.com"/></label><label>Plan activo<select required value={planId} onChange={(e) => setPlanId(e.target.value)} disabled={activePlans.length === 0}><option value="">{activePlans.length ? 'Selecciona un plan activo' : 'No hay planes activos'}</option>{activePlans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>{error && <div className="alert alert-error" role="alert">{error}</div>}<div className="modal-actions"><button type="button" className="button button-secondary" onClick={onClose}>Cancelar</button><button className="button button-primary" disabled={busy || !planId || activePlans.length === 0}>{busy ? 'Enviando invitación…' : 'Enviar invitación'}</button></div></form></section></div>;
}

function UserAccountDetails() {
  const { id = '' } = useParams(); const [user, setUser] = useState<User | null>(null); const [plans, setPlans] = useState<Plan[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [saving, setSaving] = useState(false); const [notice, setNotice] = useState('');
  const [name, setName] = useState(''); const [plan, setPlan] = useState(''); const [role, setRole] = useState(''); const [status, setStatus] = useState('');
  const load = useCallback(async () => { setLoading(true); setError(''); try { const [record, catalog] = await Promise.all([api.user(id), api.plans()]); const planOptions = records<Plan>(catalog); setUser(record); setName(record.name ?? record.display_name ?? ''); setPlan(typeof record.plan === 'string' ? planOptions.find((p) => p.id === record.plan || p.name === record.plan)?.id ?? record.plan : record.plan?.id ?? planOptions.find((p) => p.name === record.plan?.name)?.id ?? ''); setRole(record.role ?? record.roles?.[0] ?? 'user'); setStatus(record.status ?? (record.is_active === false ? 'inactive' : 'active')); setPlans(planOptions); } catch (e) { setError(errText(e)); } finally { setLoading(false); } }, [id]);
  useEffect(() => { load(); }, [load]);
  const save = async (event: FormEvent) => { event.preventDefault(); if (!user) return; setSaving(true); setError(''); setNotice(''); try { const updated = await api.updateUser(id, { display_name: name.trim(), planId: plans.find((p) => p.id === plan)?.id, role, status }); setUser({ ...user, ...updated }); setNotice('Cambios guardados correctamente.'); } catch (e) { setError(errText(e)); } finally { setSaving(false); } };
  if (loading) return <Loading/>; if (error && !user) return <ErrorState error={error} retry={load}/>; if (!user) return null;
  return <><div className="back-link"><Link to="/users">← Volver a usuarios</Link></div><PageHeader eyebrow="CUENTA DE USUARIO" title={displayName(user)} description={user.email} action={<StatusBadge status={status}/>}/>{notice && <div className="alert alert-success" role="status">{notice}</div>}{error && <div className="alert alert-error" role="alert">{error}</div>}<div className="user-detail-grid"><section className="panel detail-form-panel"><div className="panel-header"><div><h2>Información de cuenta</h2><p>Actualiza acceso y asignación de plan.</p></div></div><form className="form-stack" onSubmit={save}><label>Nombre<input value={name} onChange={(e) => setName(e.target.value)}/></label><label>Correo electrónico<input value={user.email} disabled/><small className="field-hint">El correo no se puede cambiar desde esta vista.</small></label><label>Plan<select value={plan} onChange={(e) => setPlan(e.target.value)}><option value="">No especificado</option>{plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}{plan && !plans.some((p) => p.id === plan) && !plans.some((p) => p.name === plan) && <option value={plan}>{plan}</option>}</select></label><label>Rol<select value={role} onChange={(e) => setRole(e.target.value)}><option value="user">Usuario</option><option value="admin">Administrador</option></select></label><label>Estado<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="active">Activo</option><option value="inactive">Inactivo</option><option value="suspended">Suspendido</option></select></label><button className="button button-primary align-start" disabled={saving}>{saving ? 'Guardando…' : 'Guardar cambios'}</button></form></section><aside className="panel account-info"><div className="profile-avatar-large"><Avatar label={displayName(user)}/></div><h2>{displayName(user)}</h2><p>{user.email}</p><div className="info-list"><div><span>Identificador</span><code>{user.id}</code></div><div><span>Creada</span><strong>{dateLabel(user.createdAt ?? user.created_at)}</strong></div><div><span>Verificación de correo</span><StatusBadge status={user.emailVerified ?? user.email_verified ? 'verified' : 'pending'}/></div></div></aside></div></>;
}

function UserDetailPage() {
  return <><UserAccountDetails/><UserLimitOverridesPanel/></>;
}

function UserLimitOverridesPanel() {
  const { id = '' } = useParams();
  const [user, setUser] = useState<User | null>(null); const [draft, setDraft] = useState<UserLimitsDraft | null>(null);
  const [loading, setLoading] = useState(true); const [saving, setSaving] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const load = useCallback(() => { setLoading(true); setError(''); api.user(id).then((record) => { setUser(record); setDraft(initialUserLimitDraft(record)); }).catch((e) => setError(errText(e))).finally(() => setLoading(false)); }, [id]);
  useEffect(() => { load(); }, [load]);
  const updateNumeric = (key: (typeof userLimitFields)[number][0] | 'max_ocr_units_month', changes: Partial<LimitOverrideDraft>) => {
    setDraft((current) => current ? { ...current, [key]: { ...current[key], ...changes } } : current);
  };
  const updateOcrMode = (mode: 'inherit' | 'enabled' | 'disabled') => setDraft((current) => current ? { ...current, ocr_enabled: { ...current.ocr_enabled, mode } } : current);
  const save = async (event: FormEvent) => {
    event.preventDefault(); if (!draft || !user) return;
    setError(''); setNotice('');
    const limits: Record<string, number | boolean | null> = {};
    for (const [key] of userLimitFields) {
      const setting = draft[key];
      if (setting.mode === 'inherit') { limits[key] = null; continue; }
      if (!setting.value.trim()) { setError(`Indica un valor para ${key} o selecciona “Heredar del plan”.`); return; }
      const parsed = Number(setting.value);
      if (!Number.isFinite(parsed) || parsed < 0 || (!['max_input_bytes_month', 'max_file_bytes'].includes(key) && !Number.isInteger(parsed))) { setError(`El valor de ${key} debe ser numérico y válido.`); return; }
      const value = ['max_input_bytes_month', 'max_file_bytes'].includes(key) ? Math.round(parsed * 1024 * 1024) : parsed;
      if (key === 'requests_per_minute' && value <= 0) { setError('Las solicitudes por minuto deben ser mayores a cero.'); return; }
      if (key === 'max_file_bytes' && value <= 0) { setError('El máximo por archivo debe ser mayor a cero.'); return; }
      if (key === 'max_file_bytes' && value > 100 * 1024 * 1024) { setError('El máximo de archivo no puede superar el límite global de 100 MiB.'); return; }
      limits[key] = value;
    }
    limits.ocr_enabled = draft.ocr_enabled.mode === 'inherit' ? null : draft.ocr_enabled.mode === 'enabled';
    if (draft.max_ocr_units_month.mode === 'inherit') limits.max_ocr_units_month = null;
    else {
      const parsed = Number(draft.max_ocr_units_month.value);
      if (!draft.max_ocr_units_month.value.trim() || !Number.isInteger(parsed) || parsed < 0) { setError('Las unidades OCR/mes deben ser un entero no negativo o heredarse del plan.'); return; }
      limits.max_ocr_units_month = parsed;
    }
    setSaving(true);
    try {
      const updated = await api.updateUser(id, { limits });
      setUser((current) => current ? { ...current, ...updated } : updated);
      setNotice('Overrides guardados. Los valores efectivos se actualizaron desde el API.');
    } catch (e) { setError(errText(e)); }
    finally { setSaving(false); }
  };
  const updateField = (key: (typeof userLimitFields)[number][0] | 'max_ocr_units_month', mode: 'inherit' | 'explicit') => updateNumeric(key, { mode, value: mode === 'inherit' ? '' : draft?.[key].value ?? '' });
  if (loading) return <section className="panel overrides-panel"><Loading/></section>;
  if (error && !user) return <section className="panel overrides-panel"><ErrorState error={error} retry={load}/></section>;
  if (!user || !draft) return null;
  const effective = user.limits ?? {};
  return <section className="panel overrides-panel"><div className="panel-header"><div><h2>Límites por usuario</h2><p>Cada valor puede heredarse del plan o sobrescribirse para esta cuenta.</p></div><span className="secure-label"><Icon name="settings" size={15}/> Overrides</span></div>
    {error && <div className="alert alert-error override-alert" role="alert">{error}</div>}{notice && <div className="alert alert-success override-alert" role="status">{notice}</div>}
    <form className="overrides-form" onSubmit={save}>
      <div className="override-grid">
        {userLimitFields.map(([key, label, unit]) => {
          const setting = draft[key]; const isBytes = key === 'max_input_bytes_month' || key === 'max_file_bytes';
          const value = setting.value;
          const effectiveValue = effectiveLimitLabel(effective[key], key);
          return <div className="override-field" key={key}><div className="override-field-heading"><strong>{label}</strong><small>Efectivo: {effectiveValue}</small></div><label className="override-mode-label">Origen<select aria-label={`${label}: modo`} value={setting.mode} onChange={(event) => updateField(key, event.target.value as 'inherit' | 'explicit')}><option value="inherit">Heredar del plan</option><option value="explicit">Valor personalizado</option></select></label>{setting.mode === 'explicit' && <label className="override-value-label">Valor<input type="number" min={key === 'requests_per_minute' || key === 'max_file_bytes' ? (key === 'max_file_bytes' ? '0.00000001' : 1) : 0} max={key === 'max_file_bytes' ? '100' : undefined} step={isBytes ? '0.00000001' : '1'} required value={value} onChange={(event) => updateNumeric(key, { value: event.target.value })}/><span>{unit}</span></label>}</div>;
        })}
        <div className="override-field"><div className="override-field-heading"><strong>OCR por plan</strong><small>Efectivo: {effectiveLimitLabel(effective.ocr_enabled, 'ocr_enabled')}</small></div><label className="override-mode-label">Origen<select aria-label="OCR: modo" value={draft.ocr_enabled.mode} onChange={(event) => updateOcrMode(event.target.value as 'inherit' | 'enabled' | 'disabled')}><option value="inherit">Heredar del plan</option><option value="enabled">Habilitar para esta cuenta</option><option value="disabled">Deshabilitar para esta cuenta</option></select></label></div>
        <div className="override-field"><div className="override-field-heading"><strong>Unidades OCR por mes</strong><small>Efectivo: {effectiveLimitLabel(effective.max_ocr_units_month, 'max_ocr_units_month')}</small></div><label className="override-mode-label">Origen<select aria-label="Unidades OCR: modo" value={draft.max_ocr_units_month.mode} onChange={(event) => updateField('max_ocr_units_month', event.target.value as 'inherit' | 'explicit')}><option value="inherit">Heredar del plan</option><option value="explicit">Valor personalizado</option></select></label>{draft.max_ocr_units_month.mode === 'explicit' && <label className="override-value-label">Valor<input type="number" min="0" step="1" required value={draft.max_ocr_units_month.value} onChange={(event) => updateNumeric('max_ocr_units_month', { value: event.target.value })}/><span>unidades</span></label>}</div>
      </div>
      <div className="override-actions"><p>Los campos en modo heredado se envían como <code>null</code>. Los valores efectivos mostrados provienen del API. El máximo global de archivo es 100 MiB.</p><button className="button button-primary" disabled={saving}>{saving ? 'Guardando…' : 'Guardar límites'}</button></div>
    </form>
  </section>;
}

function ReportsPage() {
  const [range, setRange] = useState('30d'); const [data, setData] = useState<unknown>(null); const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [exporting, setExporting] = useState(false);
  const query = useMemo(() => { const end = new Date(); const start = new Date(end); if (range === '7d') start.setDate(end.getDate() - 7); else if (range === '90d') start.setDate(end.getDate() - 90); else start.setDate(end.getDate() - 30); return new URLSearchParams({ from: start.toISOString(), to: end.toISOString(), timezone: localZone }).toString(); }, [range]);
  const load = useCallback(() => { setLoading(true); setError(''); api.report(query).then(setData).catch((e) => setError(errText(e))).finally(() => setLoading(false)); }, [query]);
  useEffect(() => { load(); }, [load]);
  const body = data && !Array.isArray(data) ? data as Record<string, unknown> : {};
  const rows = records<Record<string, unknown>>(data);
  const chart = records<Record<string, unknown>>(value(body, 'series', 'daily', 'breakdown'));
  const totalConversions = value(body, 'totalConversions', 'total_conversions', 'conversionCount', 'conversion_count');
  const totalBytes = value(body, 'inputBytes', 'input_bytes', 'totalInputBytes', 'total_input_bytes');
  const succeeded = value(body, 'succeeded', 'successful', 'successCount', 'success_count');
  const exportCsv = async () => { setExporting(true); setError(''); try { const result = await api.exportReport(query); const csv = csvText(result); if (!csv) throw new Error('El API no devolvió filas para exportar.'); const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' }); const href = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = href; a.download = 'consumo-markitdown.csv'; a.click(); URL.revokeObjectURL(href); } catch (e) { setError(errText(e)); } finally { setExporting(false); } };
  const maxBar = Math.max(1, ...chart.map((item) => Number(value(item, 'count', 'conversions', 'value') ?? 0)));
  return <><PageHeader eyebrow="ANÁLISIS" title="Consumo" description={`Uso de conversiones y recursos · fechas en ${localZone}.`} action={<button className="button button-secondary" onClick={exportCsv} disabled={exporting}><Icon name="download" size={16}/>{exporting ? 'Preparando…' : 'Exportar CSV'}</button>}/>
    <div className="report-toolbar"><div className="segmented-control report-ranges" role="group" aria-label="Periodo del informe">{[['7d', '7 días'], ['30d', '30 días'], ['90d', '90 días']].map(([key, label]) => <button key={key} className={range === key ? 'segment-active' : ''} onClick={() => setRange(key)} aria-pressed={range === key}>{label}</button>)}</div><span className="timezone-label"><Icon name="clock" size={15}/>{localZone}</span></div>
    {error && <ErrorState error={error} retry={load}/>}{loading ? <Loading/> : <><section className="metric-grid report-metrics"><Metric label="Conversiones" value={totalConversions ?? rows.length ?? '—'} detail="En el periodo seleccionado" icon="file" accent="metric-blue"/><Metric label="Páginas facturables" value={(value(body, 'pageCount', 'page_count', 'quotaPages', 'quota_pages') ?? '—') as ReactNode} detail="page_count real si fiable; si no, 1 por archivo" icon="file" accent="metric-amber"/><Metric label="MB procesados" value={typeof totalBytes === 'number' ? formatBytes(totalBytes) : '—'} detail="Bytes efectivamente procesados" icon="chart" accent="metric-violet"/><Metric label="Éxitos" value={succeeded ?? '—'} detail="Los fallos no descuentan conversiones ni páginas" icon="shield" accent="metric-green"/></section><div className="plan-intro dashboard-policy"><span className="info-mark">i</span><p>Las solicitudes fallidas no descuentan conversiones ni páginas, pero sí cuentan los MB efectivamente procesados y las solicitudes por minuto. Si la paginación no es fiable (por ejemplo, imágenes), se aplica 1 unidad de página por archivo. Los datos de cuota se basan en mediciones del API.</p></div><section className="panel chart-panel"><div className="panel-header"><div><h2>Conversiones por día</h2><p>Volumen de actividad en tu zona horaria</p></div><span className="chart-legend"><i/> Conversiones</span></div>{chart.length ? <div className="bar-chart" role="img" aria-label="Gráfico de conversiones por día">{chart.slice(-28).map((item, i) => { const count = Number(value(item, 'count', 'conversions', 'value') ?? 0); const height = Math.max(4, count / maxBar * 100); const date = String(value(item, 'date', 'day', 'period') ?? ''); return <div className="bar-column" key={`${date}-${i}`} title={`${date}: ${count}`}><span className="bar-count">{count}</span><div className="bar-track"><span style={{ height: `${height}%` }}/></div><small>{date ? date.slice(5, 10) : ''}</small></div>; })}</div> : <Empty title="Sin datos para este periodo" description="Prueba con un rango más amplio o vuelve más tarde."/>}</section><section className="panel activity-panel report-table"><div className="panel-header"><div><h2>Registros de consumo</h2><p>Vista según la zona horaria local · {localZone}</p></div></div>{rows.length ? <div className="table-wrap"><table><thead><tr><th>Fecha</th><th>Cuenta</th><th>Conversión</th><th>Entrada</th><th>page_count</th><th>Resultado</th></tr></thead><tbody>{rows.map((row, i) => <tr key={String(row.id ?? i)}><td>{dateLabel(String(value(row, 'timestamp', 'createdAt', 'created_at') ?? ''))}</td><td>{String(value(row, 'email', 'accountEmail', 'account_email') ?? '—')}</td><td><span className="row-title">{String(value(row, 'conversionId', 'conversion_id', 'id') ?? '—')}</span></td><td>{typeof value(row, 'inputBytes', 'input_bytes') === 'number' ? formatBytes(Number(value(row, 'inputBytes', 'input_bytes'))) : String(value(row, 'inputMb', 'input_mb') ?? '—')}</td><td>{String(value(row, 'pageCount', 'page_count', 'quotaPages', 'quota_pages') ?? '—')}</td><td><StatusBadge status={String(value(row, 'status', 'outcome') ?? '—')}/></td></tr>)}</tbody></table></div> : <Empty title="No hay registros disponibles" description="Los registros de consumo aparecerán cuando existan conversiones en este periodo."/>}</section></>}
  </>;
}

function ProfilePage({ user, onUserChange, onLogout }: { user: User; onUserChange: (user: User) => void; onLogout: () => void }) {
  const [account, setAccount] = useState<User>(user); const [name, setName] = useState(user.name ?? user.display_name ?? ''); const [keys, setKeys] = useState<ApiKey[]>([]); const [usage, setUsage] = useState<unknown>(null); const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [saving, setSaving] = useState(false); const [keyName, setKeyName] = useState(''); const [newKey, setNewKey] = useState(''); const [busyKey, setBusyKey] = useState(false); const [sessionKey, setSessionKey] = useState(() => sessionStorage.getItem('markitdown-api-key') ?? '');
  const [currentPassword, setCurrentPassword] = useState(''); const [newPassword, setNewPassword] = useState(''); const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordBusy, setPasswordBusy] = useState(false); const [passwordError, setPasswordError] = useState(''); const [passwordNotice, setPasswordNotice] = useState('');
  const passwordTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (passwordTimer.current) clearTimeout(passwordTimer.current); }, []);
  const load = useCallback(async () => { setLoading(true); setError(''); try { const [profile, keyData, usageData] = await Promise.all([api.account(), api.keys(), api.usage('limit=5')]); setAccount(profile); setName(profile.name ?? profile.display_name ?? ''); setKeys(records<ApiKey>(keyData)); setUsage(usageData); } catch (e) { setError(errText(e)); } finally { setLoading(false); } }, []);
  useEffect(() => { load(); }, [load]);
  const saveProfile = async (event: FormEvent) => { event.preventDefault(); setSaving(true); setError(''); setNotice(''); try { const updated = await api.updateAccount({ display_name: name.trim() }); setAccount({ ...account, ...updated }); onUserChange({ ...user, ...updated }); setNotice('Perfil actualizado.'); } catch (e) { setError(errText(e)); } finally { setSaving(false); } };
  const createKey = async (event: FormEvent) => { event.preventDefault(); setBusyKey(true); setError(''); setNewKey(''); try { const result = await api.createKey(keyName.trim() || 'Clave web'); const secret = String(value(result, 'key', 'apiKey', 'api_key') ?? ''); if (!secret) throw new Error('La respuesta no incluyó la clave secreta.'); setNewKey(secret); setKeyName(''); const response = await api.keys(); setKeys(records<ApiKey>(response)); } catch (e) { setError(errText(e)); } finally { setBusyKey(false); } };
  const activateKey = () => { sessionStorage.setItem('markitdown-api-key', newKey); setSessionKey(newKey); setNewKey(''); setNotice('Clave disponible para conversiones durante esta pestaña.'); };
  const revokeKey = async (id: string) => { setError(''); try { await api.revokeKey(id); setKeys((old) => old.filter((key) => key.id !== id)); setNotice('Clave revocada.'); } catch (e) { setError(errText(e)); } };
  const changePassword = async (event: FormEvent) => {
    event.preventDefault(); setPasswordError(''); setPasswordNotice('');
    if (newPassword.length < 12) { setPasswordError('La nueva contraseña debe tener al menos 12 caracteres.'); return; }
    if (newPassword !== confirmPassword) { setPasswordError('La nueva contraseña y su confirmación no coinciden.'); return; }
    if (newPassword === currentPassword) { setPasswordError('La nueva contraseña debe ser distinta de la contraseña actual.'); return; }
    setPasswordBusy(true);
    try {
      await api.changePassword(currentPassword, newPassword);
      setCurrentPassword(''); setNewPassword(''); setConfirmPassword('');
      sessionStorage.removeItem('markitdown-api-key'); setSessionKey('');
      setPasswordNotice('Contraseña actualizada. Por seguridad se revocaron todas tus claves API. Cerrando sesión para que inicies de nuevo…');
      passwordTimer.current = setTimeout(() => { onLogout(); }, 2500);
    } catch (e) { setPasswordError(errText(e)); }
    finally { setPasswordBusy(false); }
  };
  const usageRows = records<Record<string, unknown>>(usage);
  return <><PageHeader eyebrow="CUENTA" title="Mi perfil" description="Administra tus datos, claves API y consulta tu actividad." />{notice && <div className="alert alert-success" role="status">{notice}<button className="alert-close" onClick={() => setNotice('')} aria-label="Cerrar">×</button></div>}{error && <ErrorState error={error} retry={load}/>}{loading ? <Loading/> : <div className="profile-grid"><div className="profile-main"><section className="panel profile-card"><div className="profile-card-heading"><Avatar label={displayName(account)}/><div><h2>{displayName(account)}</h2><p>{account.email}</p></div><StatusBadge status={account.emailVerified ?? account.email_verified ? 'verified' : 'pending'}/></div><form className="form-stack profile-form" onSubmit={saveProfile}><label>Nombre<input value={name} onChange={(e) => setName(e.target.value)} placeholder="Tu nombre"/></label><label>Correo electrónico<input type="email" value={account.email} disabled/><small className="field-hint">Para cambiar el correo, contacta al administrador de tu cuenta.</small></label><button className="button button-primary align-start" disabled={saving}>{saving ? 'Guardando…' : 'Guardar perfil'}</button></form></section>
      <section className="panel profile-card password-panel" aria-labelledby="password-panel-title"><div className="panel-header"><div><h2 id="password-panel-title">Cambiar contraseña</h2><p>Al cambiarla se revocan todas tus claves API y se cierra la sesión.</p></div><span className="secure-label"><Icon name="shield" size={15}/> Seguridad</span></div>
        <form className="form-stack profile-form" onSubmit={changePassword}>
          <label>Contraseña actual<input type="password" autoComplete="current-password" required value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} placeholder="Tu contraseña actual" aria-describedby="current-password-hint" disabled={passwordBusy || Boolean(passwordNotice)}/><small className="field-hint" id="current-password-hint">La necesitamos para confirmar el cambio.</small></label>
          <label>Nueva contraseña<input type="password" autoComplete="new-password" required minLength={12} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="Mínimo 12 caracteres" aria-describedby="new-password-hint" disabled={passwordBusy || Boolean(passwordNotice)}/><small className="field-hint" id="new-password-hint">Usa al menos 12 caracteres y evita reutilizar una contraseña anterior.</small></label>
          <label>Confirmar nueva contraseña<input type="password" autoComplete="new-password" required minLength={12} value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} placeholder="Vuelve a escribir la nueva contraseña" aria-describedby="confirm-password-hint" disabled={passwordBusy || Boolean(passwordNotice)}/><small className="field-hint" id="confirm-password-hint">Debe coincidir exactamente con la nueva contraseña.</small></label>
          {passwordError && <div className="alert alert-error" role="alert">{passwordError}</div>}
          {passwordNotice && <div className="alert alert-success" role="status" aria-live="polite">{passwordNotice} <button type="button" className="text-button" onClick={onLogout}>Ir a iniciar sesión ahora</button></div>}
          <p className="small-note">Las claves API existentes se revocan al cambiar la contraseña; deberás crear una nueva para convertir documentos.</p>
          <button className="button button-primary align-start" disabled={passwordBusy || Boolean(passwordNotice)}>{passwordBusy ? <><span className="spinner spinner-light"/> Actualizando…</> : 'Cambiar contraseña'}</button>
        </form>
      </section>
      <section className="panel keys-panel"><div className="panel-header"><div><h2>Claves API</h2><p>Las claves secretas solo se muestran una vez.</p></div><span className="secure-label"><Icon name="shield" size={15}/> Protegidas</span></div><form className="key-create" onSubmit={createKey}><label className="sr-only" htmlFor="key-name">Nombre de clave API</label><input id="key-name" value={keyName} onChange={(e) => setKeyName(e.target.value)} placeholder="Nombre de la clave (ej. Mi sitio web)"/><button className="button button-primary" disabled={busyKey}><Icon name="plus" size={16}/>{busyKey ? 'Creando…' : 'Crear clave'}</button></form>{newKey && <div className="new-key-callout"><div><strong>Guarda esta clave ahora</strong><p>Por seguridad no volverá a mostrarse. Se usará solo en memoria de esta pestaña para convertir.</p><code>{newKey}</code></div><button className="button button-secondary" onClick={activateKey}>Usar en esta pestaña</button></div>}{sessionKey && <div className="session-key-callout"><span className="status-badge badge-good"><i/> Clave de conversión configurada en esta pestaña</span><button className="text-button" onClick={() => { sessionStorage.removeItem('markitdown-api-key'); setSessionKey(''); }}>Quitar</button></div>}{keys.length ? <div className="key-list">{keys.map((item) => <div className="key-row" key={item.id}><span className="key-symbol"><Icon name="shield" size={16}/></span><div><strong>{item.name || 'Clave API'}</strong><small><code>{item.prefix ? `${item.prefix}••••••••` : '••••••••••••'}</code> · creada {dateLabel(item.createdAt ?? item.created_at)}</small></div><span className="key-last-used">{item.lastUsedAt || item.last_used_at ? `Usada ${dateLabel(item.lastUsedAt ?? item.last_used_at)}` : 'Sin uso registrado'}</span><button className="text-button danger-text" onClick={() => revokeKey(item.id)}>Revocar</button></div>)}</div> : <Empty title="Sin claves API" description="Crea una clave para habilitar conversiones desde esta interfaz."/>}<p className="small-note">Evita compartir claves. Esta app no las guarda en localStorage ni las envía a analítica.</p></section>
      </div><aside className="profile-side"><section className="panel usage-summary"><div className="panel-header"><div><h2>Uso reciente</h2><p>Registros de tu cuenta</p></div></div>{usageRows.length ? usageRows.slice(0, 5).map((row, i) => <div className="usage-row" key={String(row.id ?? i)}><span className="usage-icon"><Icon name="file" size={15}/></span><div><strong>{String(value(row, 'inputType', 'input_type', 'extension') ?? 'Conversión')}</strong><small>{dateLabel(String(value(row, 'timestamp', 'createdAt', 'created_at') ?? ''))}</small></div><StatusBadge status={String(value(row, 'status', 'outcome') ?? '—')}/></div>) : <Empty title="Sin actividad" description="No hay registros disponibles."/>}<Link to="/dashboard" className="inline-link">Ver resumen <Icon name="arrow" size={14}/></Link></section><section className="policy-card"><div className="policy-icon"><Icon name="shield" size={19}/></div><strong>Privacidad por diseño</strong><p>Los resultados Markdown se muestran desde memoria y no se almacenan en tu cuenta después de esta sesión.</p></section></aside></div>}</>;
}
