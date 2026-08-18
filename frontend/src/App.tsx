import { FormEvent, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "./api/client";
import type {
  AutoPlan,
  GalleryPage,
  HardwareInfo,
  HealthReport,
  Project,
  ProjectStorage,
  Recipe,
  ReviewItem,
  Run,
  ResourceInventory,
  SystemStatus,
  TaggerInfo,
} from "./api/types";

type ProjectSection =
  | "overview"
  | "gallery"
  | "visual-search"
  | "health"
  | "new"
  | "prepare"
  | "expert"
  | "review"
  | "history";

function ProjectShell({
  project,
  section,
  onNavigate,
  onHome,
  onResources,
  children,
}: {
  project: Project;
  section: ProjectSection;
  onNavigate: (section: ProjectSection) => void;
  onHome: () => void;
  onResources: () => void;
  children: ReactNode;
}) {
  const items: Array<[ProjectSection, string, string]> = [
    ["overview", "Обзор", "01"],
    ["gallery", "Галерея", "02"],
    ["health", "Здоровье", "03"],
    ["visual-search", "Визуальный поиск", "03"],
    ["review", "Проверка", "04"],
    ["history", "История", "05"],
  ];
  return (
    <div className="project-shell">
      <header className="project-bar">
        <button className="project-home" onClick={onHome} title="Все проекты">
          <BrandMark />
        </button>
        <div className="project-identity">
          <small>Текущий проект</small>
          <b>{project.name}</b>
          <span>{project.dataset_path}</span>
        </div>
        <nav aria-label="Разделы проекта">
          {items.map(([id, label, index]) => (
            <button
              key={id}
              className={section === id ? "active" : ""}
              onClick={() => onNavigate(id)}
            >
              <i>{index}</i>
              <span>{label}</span>
            </button>
          ))}
          <button className="new-run-entry" onClick={() => onNavigate("new")}>
            <i>+</i>
            <span>Новый запуск</span>
          </button>
          <button className="resource-entry" onClick={onResources}>
            <i>••</i>
            <span>Ресурсы</span>
          </button>
        </nav>
      </header>
      <div className="project-content">{children}</div>
    </div>
  );
}

function ProjectGallery({
  project,
  onBack,
  onRun,
  onFindSimilar,
}: {
  project: Project;
  onBack: () => void;
  onRun: (id: string) => void;
  onFindSimilar: (path: string) => void;
}) {
  const [result, setResult] = useState<GalleryPage | null>(null);
  const [search, setSearch] = useState("");
  const [missingOnly, setMissingOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [openPath, setOpenPath] = useState("");
  const [caption, setCaption] = useState("");
  const [feedback, setFeedback] = useState("");
  const [working, setWorking] = useState(false);
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());
  const [bulkValue, setBulkValue] = useState("");
  const [bulkFeedback, setBulkFeedback] = useState("");
  const [error, setError] = useState("");
  const [confirmation, setConfirmation] = useState<{
    title: string;
    message: string;
    action: () => void;
  } | null>(null);
  const [pendingPath, setPendingPath] = useState<string | null>(null);
  useEffect(() => {
    const timer = setTimeout(
      () =>
        api
          .gallery(project.id, search, missingOnly, page)
          .then(setResult)
          .catch((reason: Error) => setError(reason.message)),
      180,
    );
    return () => clearTimeout(timer);
  }, [project.id, search, missingOnly, page]);
  useEffect(() => setPage(1), [search, missingOnly]);
  const selected = result?.items.find((item) => item.path === openPath);
  useEffect(() => {
    if (selected) {
      setCaption(selected.caption);
      setFeedback("");
    }
  }, [selected?.path]);
  async function saveCaption() {
    if (!selected) return;
    setWorking(true);
    setError("");
    try {
      const saved = await api.saveCaption(project.id, selected.path, caption);
      setResult((current) =>
        current
          ? {
              ...current,
              items: current.items.map((item) =>
                item.path === selected.path ? { ...item, ...saved } : item,
              ),
            }
          : current,
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Caption не сохранён",
      );
    } finally {
      setWorking(false);
    }
  }
  async function regenerate() {
    if (!selected) return;
    setWorking(true);
    setError("");
    try {
      const run = await api.regenerateGalleryItem(
        project.id,
        selected.path,
        feedback,
      );
      onRun(run.run_id);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Перегенерация не запущена",
      );
      setWorking(false);
    }
  }
  function toggleSelection(path: string) {
    setSelectedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }
  function togglePageSelection() {
    if (!result) return;
    const paths = result.items.map((item) => item.path);
    setSelectedPaths((current) => {
      const next = new Set(current);
      const all = paths.every((path) => next.has(path));
      paths.forEach((path) => (all ? next.delete(path) : next.add(path)));
      return next;
    });
  }
  async function bulk(
    action:
      "add_tag" | "remove_tag" | "clear_caption" | "clear_caption_confirmed",
  ) {
    const paths = [...selectedPaths];
    if (!paths.length) return;
    if (action === "clear_caption") {
      setConfirmation({
        title: "Очистить captions?",
        message: `Будут очищены captions у ${paths.length} выбранных изображений. Это действие изменит файлы dataset.`,
        action: () => void bulk("clear_caption_confirmed"),
      });
      return;
    }
    setWorking(true);
    setError("");
    try {
      const response = await api.galleryBulk(
        project.id,
        paths,
        action === "clear_caption_confirmed" ? "clear_caption" : action,
        bulkValue,
      );
      setResult((current) =>
        current
          ? {
              ...current,
              items: current.items.map((item) =>
                response.updated.find((value) => value.path === item.path)
                  ? {
                      ...item,
                      ...response.updated.find(
                        (value) => value.path === item.path,
                      )!,
                    }
                  : item,
              ),
            }
          : current,
      );
      setSelectedPaths(new Set());
      setBulkValue("");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Массовое действие не выполнено",
      );
    } finally {
      setWorking(false);
    }
  }
  async function regenerateSelected() {
    const paths = [...selectedPaths];
    if (!paths.length) return;
    setWorking(true);
    setError("");
    try {
      const run = await api.regenerateGalleryItems(
        project.id,
        paths,
        bulkFeedback,
      );
      onRun(run.run_id);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Перегенерация не запущена",
      );
      setWorking(false);
    }
  }
  function navigateEditor(path: string) {
    if (selected && caption !== selected.caption) {
      setPendingPath(path);
      return;
    }
    setOpenPath(path);
  }
  if (selected) {
    const index = result!.items.indexOf(selected);
    return (
      <main className="overview gallery-editor">
        {pendingPath !== null && (
          <ConfirmDialog
            title="Не сохранять изменения?"
            message="Caption был изменён. При переходе введённый текст будет потерян."
            destructive
            onCancel={() => setPendingPath(null)}
            onConfirm={() => {
              const path = pendingPath;
              setPendingPath(null);
              setOpenPath(path);
            }}
          />
        )}
        <div className="gallery-editor-nav">
          <button className="back" onClick={() => navigateEditor("")}>
            ← К сетке
          </button>
          <span>
            {index + 1} / {result!.items.length}
          </span>
          <div>
            <button
              aria-label="Предыдущее изображение"
              disabled={index === 0}
              onClick={() => navigateEditor(result!.items[index - 1].path)}
            >
              ←
            </button>
            <button
              aria-label="Следующее изображение"
              disabled={index === result!.items.length - 1}
              onClick={() => navigateEditor(result!.items[index + 1].path)}
            >
              →
            </button>
          </div>
        </div>
        <div className="gallery-editor-grid">
          <img
            src={api.imageUrl(project.id, selected.path)}
            alt={selected.name}
          />
          <section>
            <span className="eyebrow">Изображение</span>
            <h1>{selected.name}</h1>
            <p className="path">{selected.path}</p>
            <label>
              Caption
              <textarea
                value={caption}
                onChange={(e) => setCaption(e.target.value)}
                placeholder="Caption ещё не создан"
              />
            </label>
            <button
              className="ghost"
              disabled={working || caption === selected.caption}
              onClick={saveCaption}
            >
              Сохранить caption
            </button>
            <div className="gallery-regenerate">
              <label>
                Что исправить при перегенерации
                <textarea
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                  placeholder="Например: точнее описать одежду и не упоминать фон"
                />
              </label>
              <button
                className="primary"
                disabled={working}
                onClick={regenerate}
              >
                {working ? "Запускаю…" : "Перегенерировать"}
              </button>
              <button className="ghost" onClick={() => onFindSimilar(selected.path)}>Найти ещё такие</button>
              <small>
                Создаст отдельный запуск для этого изображения и сохранит его в
                истории.
              </small>
            </div>
            {error && <p className="error">{error}</p>}
          </section>
        </div>
      </main>
    );
  }
  return (
    <>
      <>
        {confirmation && (
          <ConfirmDialog
            title={confirmation.title}
            message={confirmation.message}
            destructive
            onCancel={() => setConfirmation(null)}
            onConfirm={() => {
              const action = confirmation.action;
              setConfirmation(null);
              void action();
            }}
          />
        )}
      </>
      <main className="overview project-section-page">
        <button className="back" onClick={onBack}>
          ← Обзор проекта
        </button>
        <div className="section-heading-row">
          <div>
            <span className="eyebrow">Dataset</span>
            <h1 className="page-title">Галерея</h1>
            <p className="lead">
              Просмотр изображений и captions текущего проекта.
            </p>
          </div>
          <b>{result?.total ?? 0}</b>
        </div>
        {!result && !error && (
          <div className="screen-state" role="status">
            <span className="loading-dot" /> Загружаю изображения…
          </div>
        )}
        <div className="gallery-tools">
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Поиск по имени или caption"
          />
          <label>
            <input
              type="checkbox"
              checked={missingOnly}
              onChange={(e) => setMissingOnly(e.target.checked)}
            />{" "}
            Только без caption
          </label>
          {result && result.items.length > 0 && (
            <button
              className="quiet-action gallery-select-page"
              onClick={togglePageSelection}
            >
              {result.items.every((item) => selectedPaths.has(item.path))
                ? "Снять выбор страницы"
                : "Выбрать страницу"}
            </button>
          )}
        </div>
        {selectedPaths.size > 0 && (
          <section className="gallery-bulk">
            <div>
              <b>Выбрано: {selectedPaths.size}</b>
              <button
                className="quiet-action"
                onClick={() => setSelectedPaths(new Set())}
              >
                Снять выбор
              </button>
            </div>
            <div className="gallery-bulk-row">
              <input
                value={bulkValue}
                onChange={(e) => setBulkValue(e.target.value)}
                placeholder="Тег для массового действия"
              />
              <button
                className="ghost"
                disabled={!bulkValue.trim() || working}
                onClick={() => bulk("add_tag")}
              >
                Добавить тег
              </button>
              <button
                className="ghost"
                disabled={!bulkValue.trim() || working}
                onClick={() => bulk("remove_tag")}
              >
                Удалить тег
              </button>
              <button
                className="danger-quiet"
                disabled={working}
                onClick={() => bulk("clear_caption")}
              >
                Очистить captions
              </button>
            </div>
            <div className="gallery-bulk-row">
              <input
                value={bulkFeedback}
                onChange={(e) => setBulkFeedback(e.target.value)}
                placeholder="Общее замечание для перегенерации"
              />
              <button
                className="primary"
                disabled={working}
                onClick={regenerateSelected}
              >
                Перегенерировать выбранные
              </button>
            </div>
          </section>
        )}
        {error && <p className="error">{error}</p>}
        {result && result.items.length === 0 && (
          <div className="screen-state empty-gallery">
            <b>Ничего не найдено</b>
            <span>Измените запрос или отключите фильтр.</span>
          </div>
        )}
        <section className="gallery-grid">
          {result?.items.map((item) => (
            <article
              key={item.path}
              className={selectedPaths.has(item.path) ? "selected" : ""}
            >
              <label title="Выбрать">
                <input
                  type="checkbox"
                  checked={selectedPaths.has(item.path)}
                  onChange={() => toggleSelection(item.path)}
                />
              </label>
              <button onClick={() => setOpenPath(item.path)}>
                <img
                  loading="lazy"
                  src={api.imageUrl(project.id, item.path)}
                  alt={item.name}
                />
                <span>
                  <b>{item.name}</b>
                  <small>{item.caption || "Без caption"}</small>
                </span>
              </button>
            </article>
          ))}
        </section>
        {result && result.pages > 1 && (
          <div className="gallery-pagination">
            <button disabled={page <= 1} onClick={() => setPage(page - 1)}>
              ←
            </button>
            <span>
              {page} / {result.pages}
            </span>
            <button
              disabled={page >= result.pages}
              onClick={() => setPage(page + 1)}
            >
              →
            </button>
          </div>
        )}
      </main>
    </>
  );
}

function VisualSearch({
  project,
  onRun,
  initialReferences = [],
}: {
  project: Project;
  onRun: (id: string) => void;
  initialReferences?: string[];
}) {
  const [gallery, setGallery] = useState<GalleryPage | null>(null);
  const [refs, setRefs] = useState<Set<string>>(new Set(initialReferences));
  const [result, setResult] = useState<any>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [breadth, setBreadth] = useState<"precise" | "balanced" | "broad">("balanced");
  const [threshold, setThreshold] = useState(0.8);
  const [mode, setMode] = useState("overall");
  const [query, setQuery] = useState("");
  const [tag, setTag] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    api
      .gallery(project.id, "", false, 1, 200)
      .then(setGallery)
      .catch((e: Error) => setError(e.message));
  }, [project.id]);
  async function search() {
    if (!refs.size) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.visualSearch(
        project.id,
        [...refs],
        200,
        threshold,
        mode,
        query,
      );
      setResult(response);
      setSelected(new Set());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Поиск не выполнен");
    } finally {
      setBusy(false);
    }
  }
  async function action(
    kind: "add_tag" | "remove_tag" | "clear_caption" | "regenerate",
  ) {
    const paths = [...selected];
    if (!paths.length) return;
    if (
      kind === "clear_caption" &&
      !window.confirm(`Очистить caption у ${paths.length} файлов?`)
    )
      return;
    setBusy(true);
    setError("");
    try {
      if (kind === "regenerate") {
        const run = await api.regenerateGalleryItems(
          project.id,
          paths,
          "Перегенерировать caption с учётом найденной визуальной группы",
        );
        onRun(run.run_id);
        return;
      } else {
        if (kind !== "clear_caption" && !tag.trim())
          throw new Error("Укажите тег");
        await api.galleryBulk(project.id, paths, kind, tag);
      }
      setSelected(new Set());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Действие не выполнено");
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="overview project-section-page visual-search-page">
      <span className="eyebrow">Визуальный контекст</span>
      <div className="section-heading-row">
        <div>
          <h1 className="page-title">Визуальный поиск</h1>
          <p className="lead">
            Выберите примеры и найдите изображения с похожей композицией и
            сценой.
          </p>
        </div>
        <b>{result?.items?.length ?? 0}</b>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="gallery-tools">
        <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Режим поиска">
          <option value="overall">Общий контекст</option>
          <option value="pose_action">Поза и действие</option>
          <option value="composition">Композиция и ракурс</option>
          <option value="theme">Тема и объекты</option>
        </select>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Уточнение: держится за ручки, вид сбоку" />
        <button
          className="primary"
          disabled={!refs.size || busy}
          onClick={search}
        >
          {busy ? "Ищу…" : `Найти похожие (${refs.size})`}
        </button>
        <div className="visual-breadth" role="group" aria-label="Ширина выдачи">
          <button className={breadth === "precise" ? "active" : ""} onClick={() => { setBreadth("precise"); setThreshold(0.86); }}>Точно</button>
          <button className={breadth === "balanced" ? "active" : ""} onClick={() => { setBreadth("balanced"); setThreshold(0.8); }}>Сбалансировано</button>
          <button className={breadth === "broad" ? "active" : ""} onClick={() => { setBreadth("broad"); setThreshold(0.72); }}>Шире</button>
        </div>
        <label>
          Порог совпадения{" "}
          <input
            type="range"
            min="0.55"
            max="0.95"
            step="0.01"
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
          />
          <b>{Math.round(threshold * 100)}%</b>
        </label>
        <button
          className="ghost"
          disabled={busy}
          onClick={() => api.rebuildVisualSearchIndex(project.id)}
        >
          Обновить индекс
        </button>
      </div>
      <h2>Примеры для поиска</h2>
      <section className="gallery-grid">
        {gallery?.items.map((item) => (
          <article
            key={item.path}
            className={refs.has(item.path) ? "selected" : ""}
            onClick={() => setRefs((current) => { const n = new Set(current); n.has(item.path) ? n.delete(item.path) : n.add(item.path); return n; })}
          >
            <label>
              <input
                type="checkbox"
                checked={refs.has(item.path)}
                onClick={(event) => event.stopPropagation()}
                onChange={() =>
                  setRefs((current) => {
                    const n = new Set(current);
                    n.has(item.path) ? n.delete(item.path) : n.add(item.path);
                    return n;
                  })
                }
              />
            </label>
            <img
              loading="lazy"
              src={api.imageUrl(project.id, item.path)}
              alt={item.name}
            />
            <span>
              <b>{item.name}</b>
              <small>{item.caption || "Без caption"}</small>
            </span>
          </article>
        ))}
      </section>
      {result && (
        <section className="visual-results">
        <>
          <h2>Найденные изображения</h2>
          <button className="quiet-action" onClick={() => setSelected((current) => current.size === result.items.length ? new Set() : new Set(result.items.map((item: any) => item.path)))}>{selected.size === result.items.length ? "Снять выбор результатов" : "Выбрать все результаты"}</button>
          {selected.size > 0 && (
            <section className="gallery-bulk">
              <b>Выбрано: {selected.size}</b>
              <div className="gallery-bulk-row">
                <input
                  value={tag}
                  onChange={(e) => setTag(e.target.value)}
                  placeholder="Тег"
                />
                <button
                  className="ghost"
                  disabled={busy || !tag.trim()}
                  onClick={() => action("add_tag")}
                >
                  Добавить тег
                </button>
                <button
                  className="ghost"
                  disabled={busy || !tag.trim()}
                  onClick={() => action("remove_tag")}
                >
                  Удалить тег
                </button>
                <button
                  className="danger-quiet"
                  disabled={busy}
                  onClick={() => action("clear_caption")}
                >
                  Очистить captions
                </button>
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => action("regenerate")}
                >
                  Перегенерировать
                </button>
              </div>
            </section>
          )}
          <section className="gallery-grid">
            {result.items.map((item: any) => (
              <article
                key={item.path}
                className={selected.has(item.path) ? "selected" : ""}
              >
                <label>
                  <input
                    type="checkbox"
                    checked={selected.has(item.path)}
                    onChange={() =>
                      setSelected((current) => {
                        const n = new Set(current);
                        n.has(item.path)
                          ? n.delete(item.path)
                          : n.add(item.path);
                        return n;
                      })
                    }
                  />
                </label>
                <img
                  loading="lazy"
                  src={api.imageUrl(project.id, item.path)}
                  alt={item.name}
                />
                <span>
                  <b>
                    {item.name} · {(item.score * 100).toFixed(1)}%
                  </b>
                  <small>{item.reason}</small>
                </span>
              </article>
            ))}
          </section>
          {result.items.length === 0 && (
            <div className="empty-gallery">
              <b>Ничего не найдено</b>
              <span>Снизьте порог совпадения или выберите другие примеры.</span>
            </div>
          )}
        </>
        </section>
      )}
    </main>
  );
}

function ProjectHistory({
  project,
  onOpenRun,
}: {
  project: Project;
  onOpenRun: (id: string) => void;
}) {
  const [runs, setRuns] = useState<Run[]>([]);
  useEffect(() => {
    api
      .projectRuns(project.id)
      .then(setRuns)
      .catch(() => setRuns([]));
  }, [project.id]);
  return (
    <main className="overview project-section-page">
      <span className="eyebrow">Проект</span>
      <h1 className="page-title">История запусков</h1>
      <p className="lead">
        Конфигурации, результат и точка возврата к прошлой работе.
      </p>
      <section className="history-list">
        {runs.map((run) => (
          <button key={run.run_id} onClick={() => onOpenRun(run.run_id)}>
            <span className={`run-dot run-${run.status}`} />
            <div>
              <b>
                {run.scope_plan.test_drive
                  ? "Проверочный запуск"
                  : "Обработка dataset"}
              </b>
              <small>
                {new Date(run.created_at).toLocaleString("ru-RU")} ·{" "}
                {run.progress.done}/{run.progress.total}
              </small>
            </div>
            <strong>{run.status}</strong>
          </button>
        ))}
        {runs.length === 0 && <div className="empty">Запусков пока нет.</div>}
      </section>
    </main>
  );
}

function ProjectHealth({
  project,
  onOpenGallery,
  onCreateCaptions,
}: {
  project: Project;
  onOpenGallery: () => void;
  onCreateCaptions: () => void;
}) {
  const [report, setReport] = useState<HealthReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function scan() {
    setBusy(true);
    setError("");
    try {
      setReport(await api.projectHealth(project.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Аудит не выполнен");
    } finally {
      setBusy(false);
    }
  }
  const labels: Record<string, string> = {
    broken: "Повреждённые файлы",
    orphan_captions: "Лишние captions",
    stem_collisions: "Конфликты имён",
    exact_duplicates: "Точные дубликаты",
    empty_captions: "Отсутствуют captions",
    short_captions: "Слишком короткие captions",
    unreadable_captions: "Нечитаемые captions",
    non_rgb: "Неподдерживаемый цветовой профиль",
    animated: "Анимированные изображения",
  };
  const remedies: Record<string, string> = {
    broken: "Откройте Галерею и удалите или замените повреждённые файлы.",
    orphan_captions:
      "Удалите лишние .txt через файловый менеджер после проверки.",
    stem_collisions:
      "Переименуйте конфликтующие файлы, чтобы каждому изображению соответствовал один caption.",
    exact_duplicates:
      "Оставьте один экземпляр изображения, остальные удалите после проверки.",
    empty_captions:
      "Создайте captions в режиме Auto для всех изображений без описания.",
    short_captions:
      "Откройте Галерею и перегенерируйте слишком короткие captions.",
    unreadable_captions:
      "Проверьте кодировку и сохраните caption заново в Галерее.",
    non_rgb: "Перекодируйте изображения в RGB перед запуском.",
    animated: "Выберите статичный кадр или отключите анимированные файлы.",
  };
  const actionable = report
    ? Object.entries(report.issues).filter(([, values]) => values.length > 0)
    : [];
  return (
    <main className="overview project-section-page">
      <span className="eyebrow">Качество dataset</span>
      <div className="health-heading">
        <div>
          <h1 className="page-title">Здоровье проекта</h1>
          <p className="lead">
            Проверка перед запуском. Здесь можно сразу перейти к исправлению
            найденных проблем.
          </p>
        </div>
        <button className="primary" disabled={busy} onClick={scan}>
          {busy
            ? "Проверяю…"
            : report
              ? "Проверить снова"
              : "Проверить dataset"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {!report ? (
        <div className="health-empty">
          <b>Аудит ещё не запускался</b>
          <span>
            Проверяются файлы, captions, дубли, форматы и структура dataset.
          </span>
        </div>
      ) : (
        <>
          <section className="health-summary">
            <div>
              <small>Изображения</small>
              <b>{report.images}</b>
            </div>
            <div>
              <small>С captions</small>
              <b>{report.captioned}</b>
            </div>
            <div>
              <small>Размер</small>
              <b>{(report.total_bytes / 1048576).toFixed(1)} МБ</b>
            </div>
            <div className={report.issue_count ? "attention" : "ready"}>
              <small>Проблемы</small>
              <b>{report.issue_count}</b>
            </div>
          </section>
          {actionable.length > 0 && (
            <section className="health-actions">
              <div>
                <b>Следующее действие</b>
                <span>
                  {report.issues.empty_captions?.length
                    ? "Создайте captions для изображений, где их нет."
                    : "Проверьте проблемные элементы в Gallery перед исправлением."}
                </span>
              </div>
              <div className="health-action-buttons">
                {report.issues.empty_captions?.length ? (
                  <button className="primary" onClick={onCreateCaptions}>
                    Создать captions
                  </button>
                ) : null}
                <button className="ghost" onClick={onOpenGallery}>
                  Открыть Gallery
                </button>
              </div>
            </section>
          )}
          <section className="health-results">
            {Object.entries(report.issues).map(([key, values]) => (
              <details key={key} open={values.length > 0}>
                <summary>
                  <span>{labels[key] ?? key}</span>
                  <b>{values.length}</b>
                </summary>
                {values.length ? (
                  <>
                    <p className="health-remedy">
                      {remedies[key] ?? "Проверьте элементы в Gallery."}
                    </p>
                    <div>
                      {values.slice(0, 20).map((value, index) => (
                        <code key={index}>
                          {Array.isArray(value) ? value.join(" · ") : value}
                        </code>
                      ))}
                    </div>
                  </>
                ) : (
                  <p>Проблем не найдено.</p>
                )}
              </details>
            ))}
          </section>
        </>
      )}
    </main>
  );
}

function ResourceWorkspace({ onBack }: { onBack: () => void }) {
  const [inventory, setInventory] = useState<ResourceInventory | null>(null);
  const [hardware, setHardware] = useState<HardwareInfo | null>(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [confirmation, setConfirmation] = useState<{
    title: string;
    message: string;
    destructive?: boolean;
    action: () => void;
  } | null>(null);
  const load = () =>
    Promise.all([api.resources(), api.hardware()]).then(
      ([resources, system]) => {
        setInventory(resources);
        setHardware(system);
      },
    );
  useEffect(() => {
    load().catch((reason: Error) => setMessage(reason.message));
  }, []);
  async function install(id: string) {
    setBusy(id);
    setMessage("Загрузка модели… не закрывайте приложение.");
    try {
      await api.installTagger(id);
      await load();
      setMessage("Модель установлена");
    } catch (reason) {
      setMessage(
        reason instanceof Error ? reason.message : "Установка не выполнена",
      );
    } finally {
      setBusy("");
    }
  }
  async function remove(id: string) {
    setBusy(id);
    try {
      await api.removeTagger(id);
      await load();
      setMessage("Локальная модель удалена");
    } catch (reason) {
      setMessage(
        reason instanceof Error ? reason.message : "Удаление не выполнено",
      );
    } finally {
      setBusy("");
    }
  }
  async function installVisual(id: string) {
    setBusy(id); setMessage("Загрузка visual-модели… Не закрывайте приложение.");
    try { await api.installVisualModel(id); await load(); setMessage("Visual-модель установлена"); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "Установка не выполнена"); }
    finally { setBusy(""); }
  }
  async function removeVisual(id: string) {
    setBusy(id);
    try { await api.removeVisualModel(id); await load(); setMessage("Visual-модель удалена"); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "Удаление не выполнено"); }
    finally { setBusy(""); }
  }
  return (
    <>
      {confirmation && (
        <ConfirmDialog
          title={confirmation.title}
          message={confirmation.message}
          destructive={confirmation.destructive}
          onCancel={() => setConfirmation(null)}
          onConfirm={() => {
            const action = confirmation.action;
            setConfirmation(null);
            void action();
          }}
        />
      )}
      <main className="resources-workspace">
        <header className="resource-header">
          <button className="project-home" onClick={onBack}>
            <BrandMark />
          </button>
          <div>
            <span className="eyebrow">Общие ресурсы</span>
            <h1>Ресурсы и система</h1>
            <p>Модели и оборудование доступны всем проектам.</p>
          </div>
        </header>
        <section className="resource-section">
          <div className="resource-section-title">
            <div>
              <h2>Модели распознавания тегов</h2>
              <p>Используются в Expert pipeline и доступны всем проектам.</p>
            </div>
            <b>
              {inventory?.taggers.filter((item) => item.installed).length ?? 0}{" "}
              установлено
            </b>
          </div>
          {inventory?.taggers.map((tagger) => (
            <article className="resource-row" key={tagger.id}>
              <div
                className={`resource-state ${tagger.installed ? "installed" : "available"}`}
              />
              <div>
                <h3>{tagger.name}</h3>
                <p>{tagger.notes}</p>
                <small>
                  {tagger.repo_id} · {tagger.license} · ~
                  {(tagger.size_bytes / 1073741824).toFixed(1)} ГБ
                </small>
              </div>
              {tagger.installed ? (
                <button
                  className="danger-quiet"
                  disabled={busy === tagger.id}
                  onClick={() =>
                    setConfirmation({
                      title: "Удалить модель?",
                      message: `Локальные файлы «${tagger.name}» будут удалены с диска.`,
                      destructive: true,
                      action: () => remove(tagger.id),
                    })
                  }
                >
                  Удалить
                </button>
              ) : (
                <button
                  className="primary"
                  disabled={Boolean(busy)}
                  onClick={() =>
                    setConfirmation({
                      title: "Установить модель?",
                      message: `Загрузка «${tagger.name}» может занять больше 1 ГБ.`,
                      action: () => install(tagger.id),
                    })
                  }
                >
                  {busy === tagger.id ? "Устанавливаю…" : "Установить"}
                </button>
              )}
            </article>
          ))}
          {message && <p className="resource-message">{message}</p>}
        </section>
        <section className="resource-section">
          <div className="resource-section-title"><div><h2>Модели визуального поиска</h2><p>Локальные image embeddings для поиска сцен, поз, объектов и композиции. Внешний API не используется.</p></div><b>{inventory?.visual_models?.filter((item) => item.installed).length ?? 0} установлено</b></div>
          {inventory?.visual_models?.map((model) => <article className="resource-row" key={model.id}><div className={`resource-state ${model.installed ? "installed" : "available"}`} /><div><h3>{model.name}</h3><p>{model.notes}</p><small>{model.repo} · {model.license} · ~{(model.size_bytes / 1073741824).toFixed(1)} ГБ</small></div>{model.installed ? <button className="danger-quiet" disabled={Boolean(busy)} onClick={() => setConfirmation({title:"Удалить visual-модель?",message:`Файлы «${model.name}» будут удалены с диска.`,destructive:true,action:()=>removeVisual(model.id)})}>Удалить</button> : <button className="primary" disabled={Boolean(busy)} onClick={() => setConfirmation({title:"Установить visual-модель?",message:`Загрузка «${model.name}» займёт около ${(model.size_bytes / 1073741824).toFixed(1)} ГБ.`,action:()=>installVisual(model.id)})}>{busy === model.id ? "Устанавливаю…" : "Установить"}</button>}</article>)}
        </section>
        <section className="hardware-strip">
          <div>
            <small>Процессор</small>
            <b>
              {hardware
                ? `${hardware.physical_cores} / ${hardware.logical_cores} ядер`
                : "—"}
            </b>
          </div>
          <div>
            <small>Оперативная память</small>
            <b>
              {hardware
                ? `${(hardware.ram_available_bytes / 1073741824).toFixed(1)} ГБ`
                : "—"}
            </b>
          </div>
          <div>
            <small>Видеокарта</small>
            <b>{hardware?.gpus[0]?.name ?? "Не обнаружена"}</b>
          </div>
        </section>
      </main>
    </>
  );
}

function NewRunChooser({
  project,
  onBack,
  onAuto,
  onExpert,
}: {
  project: Project;
  onBack: () => void;
  onAuto: () => void;
  onExpert: () => void;
}) {
  return (
    <main className="overview project-section-page new-run-page">
      <button className="back" onClick={onBack}>
        ← Обзор проекта
      </button>
      <span className="eyebrow">Проект · {project.name}</span>
      <h1 className="page-title">Новый запуск</h1>
      <p className="lead">
        Выберите способ обработки dataset. Режим можно изменить до построения
        плана.
      </p>
      <div className="run-mode-grid">
        <button className="run-mode-card" onClick={onAuto}>
          <span className="run-mode-index">01</span>
          <strong>Auto</strong>
          <p>
            Рекомендованные настройки, безопасная область изменений и быстрый
            план.
          </p>
          <span className="run-mode-action">Начать в Auto →</span>
        </button>
        <button className="run-mode-card" onClick={onExpert}>
          <span className="run-mode-index">02</span>
          <strong>Expert</strong>
          <p>
            Полный контроль pipeline, prompts, sampling, runtime и политики
            review.
          </p>
          <span className="run-mode-action">Открыть Expert →</span>
        </button>
      </div>
    </main>
  );
}

function BrandMark() {
  return (
    <div className="brand-mark" aria-label="Tag Manager">
      <i />
      <b />
    </div>
  );
}
function ConfirmDialog({
  title,
  message,
  detail,
  destructive = false,
  onCancel,
  onConfirm,
}: {
  title: string;
  message: string;
  detail?: string;
  destructive?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);
  return (
    <div
      className="confirm-backdrop"
      role="presentation"
      onMouseDown={(event) =>
        event.target === event.currentTarget && onCancel()
      }
    >
      <section
        className="confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-message"
      >
        <span className="eyebrow">Требуется подтверждение</span>
        <h2 id="confirm-title">{title}</h2>
        <p id="confirm-message">{message}</p>
        {detail && <small>{detail}</small>}
        <div className="confirm-actions">
          <button ref={cancelRef} className="ghost" onClick={onCancel}>
            Отмена
          </button>
          <button
            className={destructive ? "danger-confirm" : "primary"}
            onClick={onConfirm}
          >
            {destructive ? "Подтвердить" : "Продолжить"}
          </button>
        </div>
      </section>
    </div>
  );
}
function TextInputDialog({
  title,
  message,
  value,
  placeholder,
  onChange,
  onCancel,
  onConfirm,
}: {
  title: string;
  message: string;
  value: string;
  placeholder?: string;
  onChange: (value: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);
  return (
    <div
      className="confirm-backdrop"
      role="presentation"
      onMouseDown={(event) =>
        event.target === event.currentTarget && onCancel()
      }
    >
      <section className="confirm-dialog" role="dialog" aria-modal="true">
        <span className="eyebrow">Новое название</span>
        <h2>{title}</h2>
        <p>{message}</p>
        <input
          ref={inputRef}
          className="dialog-input"
          value={value}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && value.trim()) onConfirm();
          }}
        />
        <div className="confirm-actions">
          <button className="ghost" onClick={onCancel}>
            Отмена
          </button>
          <button
            className="primary"
            disabled={!value.trim()}
            onClick={onConfirm}
          >
            Создать
          </button>
        </div>
      </section>
    </div>
  );
}
type DesktopSettings = {
  keep_background: boolean;
  autostart: boolean;
  notifications: boolean;
};

function DesktopSettingsPanel({ onClose }: { onClose: () => void }) {
  const bridge = window.pywebview?.api;
  const [settings, setSettings] = useState<DesktopSettings>({
    keep_background: false,
    autostart: false,
    notifications: true,
  });
  const [busy, setBusy] = useState(Boolean(bridge));
  const [message, setMessage] = useState(
    bridge ? "" : "Настройки окна доступны в установленной версии приложения.",
  );
  useEffect(() => {
    bridge
      ?.get_desktop_settings()
      .then(setSettings)
      .catch((reason: Error) => setMessage(reason.message))
      .finally(() => setBusy(false));
  }, [bridge]);
  async function save() {
    if (!bridge) return;
    setBusy(true);
    setMessage("");
    try {
      setSettings(await bridge.set_desktop_settings(settings));
      setMessage("Настройки сохранены");
    } catch (reason) {
      setMessage(
        reason instanceof Error
          ? reason.message
          : "Не удалось сохранить настройки",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div
      className="desktop-settings-backdrop"
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <section
        className="desktop-settings"
        role="dialog"
        aria-modal="true"
        aria-labelledby="desktop-settings-title"
      >
        <div className="desktop-settings-heading">
          <div>
            <span className="eyebrow">Приложение</span>
            <h2 id="desktop-settings-title">Поведение системы</h2>
          </div>
          <button
            className="icon-close"
            title="Закрыть"
            aria-label="Закрыть настройки"
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <label>
          <input
            type="checkbox"
            checked={settings.keep_background}
            disabled={!bridge || busy}
            onChange={(event) =>
              setSettings({
                ...settings,
                keep_background: event.target.checked,
              })
            }
          />
          <span>
            <b>Оставлять сервис в фоне</b>
            <small>
              После закрытия окна Tag Manager останется доступен в системном
              трее.
            </small>
          </span>
        </label>
        <label>
          <input
            type="checkbox"
            checked={settings.notifications}
            disabled={!bridge || busy}
            onChange={(event) =>
              setSettings({ ...settings, notifications: event.target.checked })
            }
          />
          <span>
            <b>Системные уведомления</b>
            <small>
              Сообщать о завершении, ошибках и результатах, ожидающих проверки.
            </small>
          </span>
        </label>
        <label>
          <input
            type="checkbox"
            checked={settings.autostart}
            disabled={!bridge || busy}
            onChange={(event) =>
              setSettings({ ...settings, autostart: event.target.checked })
            }
          />
          <span>
            <b>Запускать вместе с Windows</b>
            <small>
              Работает для текущего пользователя и не требует прав
              администратора.
            </small>
          </span>
        </label>
        {message && (
          <p className="desktop-settings-message" role="status">
            {message}
          </p>
        )}
        <div className="desktop-settings-actions">
          <button className="ghost" onClick={onClose}>
            Отмена
          </button>
          <button className="primary" disabled={!bridge || busy} onClick={save}>
            {busy ? "Сохраняю…" : "Сохранить"}
          </button>
        </div>
      </section>
    </div>
  );
}
function StatusBadge({ state, label }: { state: string; label: string }) {
  return (
    <div className={`status-badge status-${state}`}>
      <i />
      {label}
    </div>
  );
}
function ProgressRail({
  current,
}: {
  current: "overview" | "prepare" | "run" | "review";
}) {
  const stages = [
    ["overview", "Обзор"],
    ["prepare", "Подготовка"],
    ["run", "Запуск"],
    ["review", "Проверка"],
  ] as const;
  const currentIndex = stages.findIndex(([id]) => id === current);
  return (
    <nav className="progress-rail" aria-label="Этап проекта">
      {stages.map(([id, label], index) => (
        <span
          key={id}
          className={
            index === currentIndex
              ? "current"
              : index < currentIndex
                ? "completed"
                : "upcoming"
          }
        >
          <i>{index < currentIndex ? "✓" : index + 1}</i>
          {label}
        </span>
      ))}
    </nav>
  );
}

function ProjectOverview({
  project,
  onClose,
  onScan,
  onPrepare,
  onExpert,
  onGallery,
  onHealth,
  onReview,
  onOpenRun,
}: {
  project: Project;
  onClose: () => void;
  onScan: (recursive?: boolean) => Promise<void>;
  onPrepare: () => void;
  onExpert: () => void;
  onGallery: () => void;
  onHealth: () => void;
  onReview: () => void;
  onOpenRun: (id: string) => void;
}) {
  const scan = project.last_scan;
  const [reviewCount, setReviewCount] = useState(0);
  const [recentRuns, setRecentRuns] = useState<Run[]>([]);
  const [storage, setStorage] = useState<ProjectStorage | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState("");
  const [historyMessage, setHistoryMessage] = useState("");
  const [includeSubfolders, setIncludeSubfolders] = useState(
    Boolean(project.settings?.include_subfolders),
  );
  useEffect(() => {
    api
      .reviews(project.id)
      .then((items) => setReviewCount(items.length))
      .catch(() => {});
    api
      .projectRuns(project.id)
      .then(setRecentRuns)
      .catch(() => {});
    api
      .projectStorage(project.id)
      .then(setStorage)
      .catch(() => {});
  }, [project.id]);
  useEffect(
    () => setIncludeSubfolders(Boolean(project.settings?.include_subfolders)),
    [project.settings?.include_subfolders],
  );
  async function rescan(recursive?: boolean) {
    const previous = includeSubfolders;
    if (recursive !== undefined) setIncludeSubfolders(recursive);
    setScanning(true);
    setScanError("");
    try {
      await onScan(recursive);
    } catch (reason) {
      setIncludeSubfolders(previous);
      setScanError(
        reason instanceof Error
          ? reason.message
          : "Не удалось пересканировать dataset",
      );
    } finally {
      setScanning(false);
    }
  }
  const missing = scan?.missing_captions ?? 0;
  const total = scan?.images ?? 0;
  const ready = scan?.captions ?? 0;
  const percent = total ? Math.round((ready / total) * 100) : 0;
  return (
    <main className="overview">
      <section className="project-heading">
        <div>
          <span className="eyebrow">Текущий проект</span>
          <h1>{project.name}</h1>
          <p className="path">{project.dataset_path}</p>
        </div>
        <StatusBadge
          state={missing ? "attention" : "ready"}
          label={missing ? "Нужна подготовка" : "Готово"}
        />
      </section>
      <section className="dataset-state" aria-label="Состояние dataset">
        <div className="dataset-total">
          <span>Dataset</span>
          <strong>
            {total} <small>изображений</small>
          </strong>
        </div>
        <div className={`dataset-percent ${percent === 100 ? "complete" : ""}`}>
          <strong>{percent}%</strong>
          <div>
            <i style={{ width: `${percent}%` }} />
          </div>
        </div>
        <div className="dataset-breakdown">
          <span>
            <b>{ready}</b> готовы
          </span>
          <span className={!missing ? "muted" : "attention"}>
            <b>{missing}</b> требуют описания
          </span>
        </div>
        <label className="scope-toggle">
          <input
            type="checkbox"
            aria-label="Включая подпапки"
            disabled={scanning}
            checked={includeSubfolders}
            onChange={(event) => rescan(event.target.checked)}
          />
          <span>
            <b>Включая подпапки</b>
            <small>
              {scan
                ? `${scan.root_images} в этой папке + ${scan.nested_images} в подпапках${scan.unsupported ? ` · ${scan.unsupported} неподдерживаемых` : ""}`
                : includeSubfolders
                  ? "Обрабатываются изображения во всём дереве папок"
                  : "Обрабатываются изображения только в выбранной папке"}
            </small>
          </span>
        </label>
      </section>
      <section className="next-action">
        <div>
          <span className="eyebrow">Следующий шаг</span>
          <h2>
            {missing
              ? "Подготовить недостающие описания"
              : "Dataset готов к следующему запуску"}
          </h2>
          <p>
            {missing
              ? "Auto подберёт безопасный план и сохранит готовые captions."
              : "Можно проверить качество или создать новый план обработки."}
          </p>
        </div>
        <button className="primary primary-large" onClick={onPrepare}>
          {missing ? "Подготовить в Auto" : "Создать новый запуск"}
          <b>→</b>
        </button>
      </section>
      <div className="overview-shortcuts" aria-label="Быстрые действия">
        <button onClick={onGallery}>
          <b>Галерея</b>
          <span>Просмотреть изображения и captions →</span>
        </button>
        <button onClick={onHealth}>
          <b>Здоровье</b>
          <span>Проверить dataset перед запуском →</span>
        </button>
      </div>
      {storage && (
        <details className="project-storage">
          <summary>
            Хранилище проекта · {(storage.event_bytes / 1048576).toFixed(1)} МБ
            журналов
          </summary>
          <div>
            <span>
              {storage.run_snapshots} запусков · {storage.event_segments}{" "}
              сегментов · {storage.summaries} архивных итогов
            </span>
            <button
              onClick={async () =>
                setStorage((await api.cleanupProjectStorage(project.id)).usage)
              }
            >
              Очистить старые подробности
            </button>
          </div>
          <small>
            Итоги запусков сохраняются; удаляются только устаревшие подробные
            события по безопасной retention policy.
          </small>
        </details>
      )}
      <div className="project-tools">
        <button
          className="quiet-action"
          disabled={scanning}
          onClick={() => rescan()}
        >
          {scanning ? "Сканирую…" : "↻ Пересканировать"}
        </button>
        <button
          className={`review-action ${reviewCount ? "has-items" : ""}`}
          onClick={onReview}
        >
          <span>Открыть очередь проверки</span>
          <b>{reviewCount} элементов</b>
        </button>
      </div>
      {scanError && (
        <p className="error" role="alert">
          {scanError}
        </p>
      )}
      {recentRuns.length > 0 && (
        <section className="run-history">
          <div className="section-title">
            <h2>Последние запуски</h2>
            <span>{recentRuns.length}</span>
          </div>
          {recentRuns.slice(0, 5).map((run, index) => (
            <div className="run-history-row" key={run.run_id}>
              <button
                className="run-history-open"
                onClick={() => onOpenRun(run.run_id)}
              >
                <span className={`run-dot run-${run.status}`} />
                <div>
                  <b>
                    {run.scope_plan.test_drive
                      ? "Проверка на 3 изображениях"
                      : "Обработка dataset"}
                  </b>
                  <small>
                    {run.progress.done} / {run.progress.total} · {run.status}
                  </small>
                </div>
                <time>
                  {new Date(run.created_at).toLocaleString("ru-RU", {
                    day: "2-digit",
                    month: "short",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </time>
              </button>
              <div className="run-history-actions">
                <button
                  title="Повторить с этой конфигурацией"
                  onClick={async () => {
                    try {
                      const repeated = await api.repeatRun(run.run_id);
                      setRecentRuns((items) => [repeated.state, ...items]);
                      setHistoryMessage(
                        "Создан новый план с этой конфигурацией",
                      );
                    } catch (reason) {
                      setHistoryMessage(
                        reason instanceof Error
                          ? reason.message
                          : "Не удалось повторить конфигурацию",
                      );
                    }
                  }}
                >
                  ↻
                </button>
                {recentRuns[index + 1] && (
                  <button
                    title="Сравнить с предыдущим запуском"
                    onClick={async () => {
                      try {
                        const result = await api.compareRuns(
                          run.run_id,
                          recentRuns[index + 1].run_id,
                        );
                        const count = Object.values(result.sections).reduce(
                          (sum, items) => sum + items.length,
                          0,
                        );
                        setHistoryMessage(
                          result.identical_configuration
                            ? `Конфигурация совпадает, различий результата: ${result.sections.result.length}`
                            : `Найдено различий: ${count}`,
                        );
                      } catch (reason) {
                        setHistoryMessage(
                          reason instanceof Error
                            ? reason.message
                            : "Не удалось сравнить запуски",
                        );
                      }
                    }}
                  >
                    ⇄
                  </button>
                )}
              </div>
            </div>
          ))}
          {historyMessage && (
            <p className="history-message" role="status">
              {historyMessage}
            </p>
          )}
        </section>
      )}
    </main>
  );
}

function ExpertPrepare({
  project,
  onBack,
  onAuto,
  onPlan,
}: {
  project: Project;
  onBack: () => void;
  onAuto: () => void;
  onPlan: (plan: AutoPlan, options: Record<string, unknown>) => void;
}) {
  const [budget, setBudget] = useState(5000);
  const [scope, setScope] = useState("missing");
  const [resultType, setResultType] = useState("hybrid_caption");
  const [language, setLanguage] = useState("en");
  const [detail, setDetail] = useState("balanced");
  const [reviewPolicy, setReviewPolicy] = useState("queue");
  const [analysisMode, setAnalysisMode] = useState("accurate");
  const [triggerWord, setTriggerWord] = useState("");
  const [temperature, setTemperature] = useState(0.4);
  const [topP, setTopP] = useState(0.95);
  const [maxTokens, setMaxTokens] = useState(6144);
  const [contextSize, setContextSize] = useState(8192);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [userPrompt, setUserPrompt] = useState("");
  const [keepAlive, setKeepAlive] = useState(0);
  const [autoRetry, setAutoRetry] = useState(true);
  const [taggerIds, setTaggerIds] = useState("");
  const [taggers, setTaggers] = useState<TaggerInfo[]>([]);
  const [pipelineMode, setPipelineMode] = useState<
    "vlm" | "tagger_vlm" | "tagger_only"
  >("vlm");
  const [gpuLayers, setGpuLayers] = useState(999);
  const [threads, setThreads] = useState(
    Math.max(1, navigator.hardwareConcurrency || 8),
  );
  const [batchSize, setBatchSize] = useState(512);
  const [ubatchSize, setUbatchSize] = useState(128);
  const [cacheTypeK, setCacheTypeK] = useState("f16");
  const [cacheTypeV, setCacheTypeV] = useState("f16");
  const [flashAttention, setFlashAttention] = useState(true);
  const [useMmap, setUseMmap] = useState(true);
  const [slots, setSlots] = useState(1);
  const [additionalArgs, setAdditionalArgs] = useState("");
  const [generalThreshold, setGeneralThreshold] = useState(0.35);
  const [characterThreshold, setCharacterThreshold] = useState(0.75);
  const [includeCharacters, setIncludeCharacters] = useState(true);
  const [includeRating, setIncludeRating] = useState(false);
  const [taggerTopK, setTaggerTopK] = useState(128);
  const [taggerBlacklist, setTaggerBlacklist] = useState("");
  const [taggerAliases, setTaggerAliases] = useState("");
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [selectedRecipe, setSelectedRecipe] = useState("");
  const [recipeName, setRecipeName] = useState("");
  const [saveMessage, setSaveMessage] = useState("");
  const [compareVersion, setCompareVersion] = useState("");
  const [compareMessage, setCompareMessage] = useState("");
  const [activeRecipe, setActiveRecipe] = useState(
    `${project.active_recipe_id ?? ""}:${project.active_recipe_version ?? ""}`,
  );
  const [activeStage, setActiveStage] = useState("dataset");
  const [busy, setBusy] = useState(false);
  const [recipeDialog, setRecipeDialog] = useState<
    "clone" | "archive" | "delete" | null
  >(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api
      .recipes(project.id)
      .then(setRecipes)
      .catch(() => setRecipes([]));
    api
      .taggers()
      .then(setTaggers)
      .catch(() => setTaggers([]));
  }, [project.id]);
  function applyRecipe(value: string) {
    setSelectedRecipe(value);
    const recipe = recipes.find(
      (item) => `${item.recipe_id}:${item.version}` === value,
    );
    if (!recipe) return;
    const settings = recipe.generation_settings;
    setRecipeName(recipe.goal);
    setResultType(recipe.result_type);
    setSystemPrompt(recipe.instructions);
    setUserPrompt(recipe.prompt);
    if (typeof settings.language === "string") setLanguage(settings.language);
    if (typeof settings.detail === "string") setDetail(settings.detail);
    if (typeof settings.review_policy === "string")
      setReviewPolicy(settings.review_policy);
    if (typeof settings.analysis_mode === "string")
      setAnalysisMode(settings.analysis_mode);
    if (typeof settings.trigger_word === "string")
      setTriggerWord(settings.trigger_word);
    if (typeof settings.temperature === "number")
      setTemperature(settings.temperature);
    if (typeof settings.top_p === "number") setTopP(settings.top_p);
    if (typeof settings.max_tokens === "number")
      setMaxTokens(settings.max_tokens);
    if (typeof settings.context_size === "number")
      setContextSize(settings.context_size);
    if (typeof settings.reasoning_budget === "number")
      setBudget(settings.reasoning_budget);
    if (typeof settings.auto_retry === "boolean")
      setAutoRetry(settings.auto_retry);
    if (Array.isArray(settings.pipeline_tagger_ids))
      setTaggerIds(settings.pipeline_tagger_ids.join(", "));
    if (
      settings.pipeline_mode === "vlm" ||
      settings.pipeline_mode === "tagger_vlm" ||
      settings.pipeline_mode === "tagger_only"
    )
      setPipelineMode(settings.pipeline_mode);
    if (typeof settings.gpu_layers === "number")
      setGpuLayers(settings.gpu_layers);
    if (typeof settings.threads === "number") setThreads(settings.threads);
    if (typeof settings.batch_size === "number")
      setBatchSize(settings.batch_size);
    if (typeof settings.ubatch_size === "number")
      setUbatchSize(settings.ubatch_size);
    if (typeof settings.cache_type_k === "string")
      setCacheTypeK(settings.cache_type_k);
    if (typeof settings.cache_type_v === "string")
      setCacheTypeV(settings.cache_type_v);
    if (typeof settings.flash_attention === "boolean")
      setFlashAttention(settings.flash_attention);
    if (typeof settings.mmap === "boolean") setUseMmap(settings.mmap);
    if (typeof settings.slots === "number") setSlots(settings.slots);
    if (Array.isArray(settings.additional_args))
      setAdditionalArgs(settings.additional_args.join(" "));
    if (typeof settings.tagger_general_threshold === "number")
      setGeneralThreshold(settings.tagger_general_threshold);
    if (typeof settings.tagger_character_threshold === "number")
      setCharacterThreshold(settings.tagger_character_threshold);
    if (typeof settings.tagger_include_characters === "boolean")
      setIncludeCharacters(settings.tagger_include_characters);
    if (typeof settings.tagger_include_rating === "boolean")
      setIncludeRating(settings.tagger_include_rating);
    if (typeof settings.tagger_top_k === "number")
      setTaggerTopK(settings.tagger_top_k);
    if (Array.isArray(settings.tagger_blacklist))
      setTaggerBlacklist(settings.tagger_blacklist.join(", "));
    if (settings.tagger_aliases && typeof settings.tagger_aliases === "object")
      setTaggerAliases(
        Object.entries(settings.tagger_aliases)
          .map(([from, to]) => `${from}=${to}`)
          .join("\n"),
      );
  }
  async function saveRecipe() {
    if (!recipeName.trim()) {
      setSaveMessage("Укажите название пресета");
      return;
    }
    setSaveMessage("");
    try {
      const selected = recipes.find(
        (item) => `${item.recipe_id}:${item.version}` === selectedRecipe,
      );
      const saved = await api.saveRecipe(project.id, {
        recipe_id: selected?.recipe_id,
        name: recipeName.trim(),
        result_type: resultType,
        system_prompt: systemPrompt,
        user_prompt: userPrompt,
        settings: {
          language,
          detail,
          review_policy: reviewPolicy,
          analysis_mode: analysisMode,
          trigger_word: triggerWord,
          temperature,
          top_p: topP,
          max_tokens: maxTokens,
          context_size: contextSize,
          reasoning_budget: budget,
          auto_retry: autoRetry,
          pipeline_tagger_ids: taggerIds
            .split(",")
            .map((value) => value.trim())
            .filter(Boolean),
          pipeline_mode: pipelineMode,
          gpu_layers: gpuLayers,
          threads,
          batch_size: batchSize,
          ubatch_size: ubatchSize,
          cache_type_k: cacheTypeK,
          cache_type_v: cacheTypeV,
          flash_attention: flashAttention,
          mmap: useMmap,
          slots,
          additional_args: additionalArgs.split(/\s+/).filter(Boolean),
          tagger_general_threshold: generalThreshold,
          tagger_character_threshold: characterThreshold,
          tagger_include_characters: includeCharacters,
          tagger_include_rating: includeRating,
          tagger_top_k: taggerTopK,
          tagger_blacklist: taggerBlacklist
            .split(",")
            .map((value) => value.trim())
            .filter(Boolean),
          tagger_aliases: Object.fromEntries(
            taggerAliases
              .split(/\r?\n/)
              .map((line) => line.split("=").map((value) => value.trim()))
              .filter((pair) => pair.length === 2 && pair[0] && pair[1]),
          ),
        },
      });
      setRecipes(await api.recipes(project.id));
      setSelectedRecipe(`${saved.recipe_id}:${saved.version}`);
      setSaveMessage(`Сохранено как версия ${saved.version}`);
    } catch (reason) {
      setSaveMessage(
        reason instanceof Error
          ? reason.message
          : "Не удалось сохранить пресет",
      );
    }
  }
  async function compareRecipeVersions() {
    const selected = recipes.find(
      (item) => `${item.recipe_id}:${item.version}` === selectedRecipe,
    );
    const right = Number(compareVersion);
    if (!selected || !right || right === selected.version) return;
    try {
      const result = await api.compareRecipes(
        project.id,
        selected.recipe_id,
        selected.version,
        right,
      );
      setCompareMessage(
        result.identical
          ? "Версии совпадают"
          : `Изменений: ${result.differences.length}`,
      );
    } catch (reason) {
      setCompareMessage(
        reason instanceof Error ? reason.message : "Не удалось сравнить версии",
      );
    }
  }
  async function refreshRecipes(message: string) {
    setRecipes(await api.recipes(project.id));
    setSaveMessage(message);
  }
  async function activateSelectedRecipe() {
    const selected = recipes.find(
      (item) => `${item.recipe_id}:${item.version}` === selectedRecipe,
    );
    if (!selected) return;
    try {
      await api.activateRecipe(
        project.id,
        selected.recipe_id,
        selected.version,
      );
      setActiveRecipe(`${selected.recipe_id}:${selected.version}`);
      setSaveMessage(
        `Активный пресет: ${selected.goal} · v${selected.version}`,
      );
    } catch (reason) {
      setSaveMessage(
        reason instanceof Error
          ? reason.message
          : "Не удалось выбрать активный пресет",
      );
    }
  }
  async function cloneSelectedRecipe() {
    const selected = recipes.find(
      (item) => `${item.recipe_id}:${item.version}` === selectedRecipe,
    );
    if (!selected) return;
    setRecipeName(`${selected.goal} — копия`);
    setRecipeDialog("clone");
  }
  async function confirmCloneRecipe() {
    const selected = recipes.find(
      (item) => `${item.recipe_id}:${item.version}` === selectedRecipe,
    );
    const name = recipeName.trim();
    if (!selected || !name) return;
    setRecipeDialog(null);
    try {
      const clone = await api.cloneRecipe(
        project.id,
        selected.recipe_id,
        selected.version,
        name,
      );
      await refreshRecipes(`Создан независимый пресет ${clone.goal}`);
      setSelectedRecipe(`${clone.recipe_id}:${clone.version}`);
    } catch (reason) {
      setSaveMessage(
        reason instanceof Error
          ? reason.message
          : "Не удалось клонировать пресет",
      );
    }
  }
  async function archiveSelectedRecipe() {
    const selected = recipes.find(
      (item) => `${item.recipe_id}:${item.version}` === selectedRecipe,
    );
    if (!selected) return;
    setRecipeDialog("archive");
  }
  async function confirmArchiveRecipe() {
    const selected = recipes.find(
      (item) => `${item.recipe_id}:${item.version}` === selectedRecipe,
    );
    if (!selected) return;
    setRecipeDialog(null);
    try {
      await api.archiveRecipe(project.id, selected.recipe_id, selected.version);
      if (activeRecipe === selectedRecipe) setActiveRecipe(":");
      setSelectedRecipe("");
      await refreshRecipes("Версия перенесена в архив");
    } catch (reason) {
      setSaveMessage(
        reason instanceof Error
          ? reason.message
          : "Не удалось архивировать версию",
      );
    }
  }
  async function deleteSelectedRecipe() {
    const selected = recipes.find(
      (item) => `${item.recipe_id}:${item.version}` === selectedRecipe,
    );
    if (!selected) return;
    setRecipeDialog("delete");
  }
  async function confirmDeleteRecipe() {
    const selected = recipes.find(
      (item) => `${item.recipe_id}:${item.version}` === selectedRecipe,
    );
    if (!selected) return;
    setRecipeDialog(null);
    try {
      await api.deleteRecipe(project.id, selected.recipe_id, selected.version);
      setSelectedRecipe("");
      await refreshRecipes("Черновик удалён");
    } catch (reason) {
      setSaveMessage(
        reason instanceof Error
          ? reason.message
          : "Не удалось удалить черновик",
      );
    }
  }
  function change(value: number) {
    setBudget(
      Math.max(
        0,
        Math.min(32768, Number.isFinite(value) ? Math.round(value) : 0),
      ),
    );
  }
  function jump(stage: string, target: string) {
    setActiveStage(stage);
    document
      .getElementById(target)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pipelineMode !== "vlm" && !taggerIds.trim()) {
      setError("Выберите хотя бы одну установленную Tagger-модель");
      return;
    }
    setBusy(true);
    setError("");
    const options = {
      scope,
      detail,
      result_type: resultType,
      focus: "balanced",
      language,
      trigger_word: triggerWord,
      review_policy: reviewPolicy,
      analysis_mode: analysisMode,
      reasoning_budget: String(budget),
      temperature: String(temperature),
      top_p: String(topP),
      max_tokens: String(maxTokens),
      context_size: String(contextSize),
      ...(systemPrompt ? { system_prompt: systemPrompt } : {}),
      ...(userPrompt ? { user_prompt: userPrompt } : {}),
      keep_alive_seconds: String(keepAlive),
      auto_retry: autoRetry,
      pipeline_tagger_ids: taggerIds
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean),
      pipeline_mode: pipelineMode,
      gpu_layers: gpuLayers,
      threads,
      batch_size: batchSize,
      ubatch_size: ubatchSize,
      cache_type_k: cacheTypeK,
      cache_type_v: cacheTypeV,
      flash_attention: flashAttention,
      mmap: useMmap,
      slots,
      additional_args: additionalArgs.split(/\s+/).filter(Boolean),
      tagger_general_threshold: generalThreshold,
      tagger_character_threshold: characterThreshold,
      tagger_include_characters: includeCharacters,
      tagger_include_rating: includeRating,
      tagger_top_k: taggerTopK,
      tagger_blacklist: taggerBlacklist
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean),
      tagger_aliases: Object.fromEntries(
        taggerAliases
          .split(/\r?\n/)
          .map((line) => line.split("=").map((value) => value.trim()))
          .filter((pair) => pair.length === 2 && pair[0] && pair[1]),
      ),
    };
    try {
      onPlan(await api.autoPlan(project.id, options), options);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Не удалось построить план",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="overview expert-layout">
      <button className="back" type="button" onClick={onBack}>
        ← Обзор проекта
      </button>
      <div className="studio-mode" role="group" aria-label="Режим запуска">
        <button type="button" onClick={onAuto}>
          Auto
        </button>
        <button type="button" className="active" aria-current="page">
          Expert
        </button>
      </div>
      {recipeDialog === "clone" && (
        <TextInputDialog
          title="Клонировать пресет"
          message="Введите понятное название независимой копии."
          value={recipeName}
          onChange={setRecipeName}
          onCancel={() => setRecipeDialog(null)}
          onConfirm={() => void confirmCloneRecipe()}
        />
      )}
      {recipeDialog === "archive" && (
        <ConfirmDialog
          title="Архивировать пресет?"
          message="Версия будет убрана из активного списка, но останется доступна в истории."
          onCancel={() => setRecipeDialog(null)}
          onConfirm={() => void confirmArchiveRecipe()}
        />
      )}
      {recipeDialog === "delete" && (
        <ConfirmDialog
          title="Удалить черновик?"
          message="Черновик и его настройки будут удалены без возможности восстановления."
          destructive
          onCancel={() => setRecipeDialog(null)}
          onConfirm={() => void confirmDeleteRecipe()}
        />
      )}
      <ProgressRail current="prepare" />
      <span className="eyebrow">Экспертный конструктор</span>
      <h1 className="page-title">Полная конфигурация обработки</h1>
      <p className="lead">
        Выберите сохранённый пресет или настройте новый. Перед запуском вы
        увидите итоговую конфигурацию.
      </p>
      <form className="choice-card expert-form" onSubmit={submit}>
        <section className="recipe-toolbar" aria-label="Пресет обработки">
          <label>
            <span>Пресет</span>
            <select
              value={selectedRecipe}
              onChange={(e) => applyRecipe(e.target.value)}
            >
              <option value="">Новая конфигурация</option>
              {recipes.map((recipe) => (
                <option
                  key={`${recipe.recipe_id}:${recipe.version}`}
                  value={`${recipe.recipe_id}:${recipe.version}`}
                >
                  {recipe.status === "archived" ? "[Архив] " : ""}
                  {recipe.goal} · v{recipe.version}
                  {activeRecipe === `${recipe.recipe_id}:${recipe.version}`
                    ? " · активный"
                    : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="recipe-name">
            <span>Название</span>
            <input
              value={recipeName}
              onChange={(e) => setRecipeName(e.target.value)}
              placeholder="Например, персонажи — Danbooru EN"
            />
          </label>
          <button type="button" className="ghost" onClick={saveRecipe}>
            Сохранить пресет
          </button>
          {selectedRecipe && (
            <>
              <select
                className="recipe-compare-select"
                aria-label="Версия для сравнения"
                value={compareVersion}
                onChange={(e) => setCompareVersion(e.target.value)}
              >
                <option value="">Сравнить с…</option>
                {recipes
                  .filter(
                    (item) =>
                      item.recipe_id ===
                      recipes.find(
                        (candidate) =>
                          `${candidate.recipe_id}:${candidate.version}` ===
                          selectedRecipe,
                      )?.recipe_id,
                  )
                  .map((item) => (
                    <option key={item.version} value={item.version}>
                      v{item.version}
                    </option>
                  ))}
              </select>
              <button
                type="button"
                className="ghost"
                onClick={compareRecipeVersions}
              >
                Сравнить
              </button>
            </>
          )}
          {selectedRecipe && (
            <div className="recipe-lifecycle-actions">
              {recipes.find(
                (item) =>
                  `${item.recipe_id}:${item.version}` === selectedRecipe,
              )?.status !== "immutable" && (
                <>
                  <button
                    type="button"
                    className="ghost"
                    onClick={activateSelectedRecipe}
                  >
                    Сделать активным
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    onClick={archiveSelectedRecipe}
                  >
                    В архив
                  </button>
                  <button
                    type="button"
                    className="danger-quiet"
                    onClick={deleteSelectedRecipe}
                  >
                    Удалить черновик
                  </button>
                </>
              )}
              <button
                type="button"
                className="ghost"
                onClick={cloneSelectedRecipe}
              >
                Клонировать
              </button>
            </div>
          )}
          {saveMessage && (
            <small className="recipe-message">{saveMessage}</small>
          )}
          {compareMessage && (
            <small className="recipe-message">{compareMessage}</small>
          )}
        </section>
        <div className="studio-workspace">
          <aside className="studio-stages">
            <span>PIPELINE</span>
            <button
              type="button"
              className={activeStage === "dataset" ? "active" : ""}
              onClick={() => jump("dataset", "expert-dataset")}
            >
              <b>01</b> Данные
            </button>
            <button
              type="button"
              className={activeStage === "recipe" ? "active" : ""}
              onClick={() => jump("recipe", "expert-recipe")}
            >
              <b>02</b> Результат
            </button>
            <button
              type="button"
              className={activeStage === "review" ? "active" : ""}
              onClick={() => jump("review", "expert-review")}
            >
              <b>03</b> Проверка
            </button>
            <button
              type="button"
              className={activeStage === "prompts" ? "active" : ""}
              onClick={() => jump("prompts", "expert-prompts")}
            >
              <b>04</b> Промпты
            </button>
            <button
              type="button"
              className={activeStage === "sampling" ? "active" : ""}
              onClick={() => jump("sampling", "expert-sampling")}
            >
              <b>05</b> Генерация
            </button>
            <button
              type="button"
              className={activeStage === "memory" ? "active" : ""}
              onClick={() => jump("memory", "expert-memory")}
            >
              <b>06</b> Память
            </button>
            <button
              type="button"
              className={activeStage === "model" ? "active" : ""}
              onClick={() => jump("model", "expert-effective")}
            >
              <b>07</b> Итог
            </button>
          </aside>
          <div className="studio-editor">
            {pipelineMode !== "tagger_only" && (
              <section className="expert-sampling" id="expert-sampling">
                <h2>Сэмплирование</h2>
                <div className="sampling-grid">
                  <label>
                    Температура
                    <input
                      type="number"
                      min="0"
                      max="2"
                      step="0.05"
                      value={temperature}
                      onChange={(e) => setTemperature(Number(e.target.value))}
                    />
                  </label>
                  <label>
                    Top P (вероятность)
                    <input
                      type="number"
                      min="0"
                      max="1"
                      step="0.05"
                      value={topP}
                      onChange={(e) => setTopP(Number(e.target.value))}
                    />
                  </label>
                  <label>
                    Максимум токенов
                    <input
                      type="number"
                      min="128"
                      max="32768"
                      step="128"
                      value={maxTokens}
                      onChange={(e) => setMaxTokens(Number(e.target.value))}
                    />
                  </label>
                  <label>
                    Общий context size
                    <input
                      type="number"
                      min="1024"
                      max="131072"
                      step="1024"
                      value={contextSize}
                      onChange={(e) => setContextSize(Number(e.target.value))}
                    />
                  </label>
                </div>
              </section>
            )}
            <div className="expert-grid">
              <fieldset className="pipeline-mode-fieldset">
                <legend>Режим обработки</legend>
                <select
                  value={pipelineMode}
                  onChange={(e) =>
                    setPipelineMode(e.target.value as typeof pipelineMode)
                  }
                >
                  <option value="vlm">Только VLM</option>
                  <option value="tagger_vlm">Tagger → VLM</option>
                  <option value="tagger_only">Только Tagger</option>
                </select>
              </fieldset>
              <fieldset id="expert-dataset">
                <legend>Область</legend>
                <label>
                  <input
                    type="radio"
                    checked={scope === "missing"}
                    onChange={() => setScope("missing")}
                  />
                  <span>Только без captions</span>
                </label>
                {pipelineMode === "tagger_only" && (
                  <label>
                    <input
                      type="radio"
                      checked={scope === "augment"}
                      onChange={() => setScope("augment")}
                    />
                    <span>Дополнить существующие captions тегами</span>
                  </label>
                )}
                {pipelineMode !== "tagger_only" && (
                  <label>
                    <input
                      type="radio"
                      checked={scope === "vlm_augment"}
                      onChange={() => setScope("vlm_augment")}
                    />
                    <span>Дополнить существующие captions описанием VLM</span>
                  </label>
                )}
                <label>
                  <input
                    type="radio"
                    checked={scope === "all"}
                    onChange={() => setScope("all")}
                  />
                  <span>Весь dataset (перезапись)</span>
                </label>
              </fieldset>
              {pipelineMode !== "tagger_only" && (
                <fieldset id="expert-recipe">
                  <legend>Формат результата</legend>
                  <select
                    value={resultType}
                    onChange={(e) => setResultType(e.target.value)}
                  >
                    <option value="hybrid_caption">Теги + описание</option>
                    <option value="prose">Подробное описание</option>
                    <option value="tags">Только теги</option>
                  </select>
                </fieldset>
              )}
              {pipelineMode !== "tagger_only" && (
                <fieldset>
                  <legend>Язык captions</legend>
                  <select
                    value={language}
                    onChange={(e) => setLanguage(e.target.value)}
                  >
                    <option value="en">English (рекомендуется)</option>
                    <option value="ru">Русский</option>
                    <option value="de">Deutsch</option>
                  </select>
                </fieldset>
              )}
              {pipelineMode !== "tagger_only" && (
                <fieldset>
                  <legend>Подробность</legend>
                  <select
                    value={detail}
                    onChange={(e) => setDetail(e.target.value)}
                  >
                    <option value="concise">Кратко</option>
                    <option value="balanced">Сбалансированно</option>
                    <option value="detailed">Подробно</option>
                  </select>
                </fieldset>
              )}
              {pipelineMode !== "tagger_only" && (
                <fieldset id="expert-review">
                  <legend>Проверка</legend>
                  <select
                    value={reviewPolicy}
                    onChange={(e) => setReviewPolicy(e.target.value)}
                  >
                    <option value="queue">
                      Складывать сомнительные в очередь
                    </option>
                    <option value="stop_on_review">
                      Проверять каждый caption
                    </option>
                  </select>
                </fieldset>
              )}
              {pipelineMode !== "tagger_only" && (
                <fieldset>
                  <legend>Анализ</legend>
                  <select
                    value={analysisMode}
                    onChange={(e) => setAnalysisMode(e.target.value)}
                  >
                    <option value="fast">Быстрее (без reasoning)</option>
                    <option value="accurate">Точнее</option>
                  </select>
                </fieldset>
              )}
            </div>
            {pipelineMode !== "tagger_only" && (
              <>
                <label className="text-field">
                  <span>Trigger word (необязательно)</span>
                  <input
                    value={triggerWord}
                    onChange={(e) => setTriggerWord(e.target.value)}
                    placeholder="например, my_character"
                  />
                </label>
                <label className="reasoning-label">
                  <span>
                    <b>Лимит reasoning</b>
                    <small>
                      0 отключает размышление · максимум 32 768 токенов
                    </small>
                  </span>
                </label>
                <div className="reasoning-control">
                  <input
                    type="range"
                    min="0"
                    max="32768"
                    step="256"
                    value={budget}
                    onChange={(event) => change(Number(event.target.value))}
                  />
                  <input
                    type="number"
                    min="0"
                    max="32768"
                    step="1"
                    aria-label="Лимит reasoning"
                    value={budget}
                    onChange={(event) => change(Number(event.target.value))}
                  />
                  <span>токенов</span>
                </div>
                <div className="budget-presets">
                  {[0, 1024, 5000, 8192].map((value) => (
                    <button
                      type="button"
                      className={budget === value ? "selected" : ""}
                      key={value}
                      onClick={() => change(value)}
                    >
                      {value.toLocaleString("ru-RU")}
                    </button>
                  ))}
                </div>
              </>
            )}
            {pipelineMode !== "tagger_only" && (
              <section className="prompt-editor" id="expert-prompts">
                <h2>Промпты пресета</h2>
                <label>
                  <span>Системный prompt</span>
                  <textarea
                    value={systemPrompt}
                    onChange={(e) => setSystemPrompt(e.target.value)}
                    placeholder="Системные правила для модели"
                  />
                </label>
                <label>
                  <span>Пользовательский prompt</span>
                  <textarea
                    value={userPrompt}
                    onChange={(e) => setUserPrompt(e.target.value)}
                    placeholder="Что именно модель должна извлечь из изображения"
                  />
                </label>
              </section>
            )}
            <section className="memory-editor" id="expert-memory">
              <h2>
                {pipelineMode === "tagger_only"
                  ? "Tagger-модели"
                  : "Память и продолжение"}
              </h2>
              {pipelineMode !== "tagger_only" && (
                <label>
                  <span>Удержание модели после запуска</span>
                  <select
                    value={keepAlive}
                    onChange={(e) => setKeepAlive(Number(e.target.value))}
                  >
                    <option value={0}>Выгрузить сразу</option>
                    <option value={180}>Оставить на 3 минуты</option>
                    <option value={900}>Оставить на 15 минут</option>
                    <option value={86400}>Держать загруженной</option>
                  </select>
                </label>
              )}
              {pipelineMode !== "tagger_only" && (
                <label className="scope-toggle">
                  <input
                    type="checkbox"
                    checked={autoRetry}
                    onChange={(e) => setAutoRetry(e.target.checked)}
                  />
                  <span>
                    <b>Автоповтор при слабом результате</b>
                    <small>
                      Повторить генерацию по quality rules вместо немедленной
                      ошибки
                    </small>
                  </span>
                </label>
              )}
              {pipelineMode !== "tagger_only" && (
                <fieldset className="runtime-panel">
                  <legend>Среда выполнения llama.cpp</legend>
                  <div className="runtime-grid">
                    <label>
                      GPU layers
                      <input
                        type="number"
                        min="0"
                        max="999"
                        value={gpuLayers}
                        onChange={(e) => setGpuLayers(Number(e.target.value))}
                      />
                    </label>
                    <label>
                      Потоки
                      <input
                        type="number"
                        min="1"
                        max="512"
                        value={threads}
                        onChange={(e) => setThreads(Number(e.target.value))}
                      />
                    </label>
                    <label>
                      Batch
                      <input
                        type="number"
                        min="1"
                        max="65536"
                        value={batchSize}
                        onChange={(e) => setBatchSize(Number(e.target.value))}
                      />
                    </label>
                    <label>
                      Ubatch
                      <input
                        type="number"
                        min="1"
                        max="65536"
                        value={ubatchSize}
                        onChange={(e) => setUbatchSize(Number(e.target.value))}
                      />
                    </label>
                    <label>
                      Cache K
                      <select
                        value={cacheTypeK}
                        onChange={(e) => setCacheTypeK(e.target.value)}
                      >
                        {["f16", "q8_0", "q4_0", "f32", "bf16"].map((value) => (
                          <option key={value}>{value}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Cache V
                      <select
                        value={cacheTypeV}
                        onChange={(e) => setCacheTypeV(e.target.value)}
                      >
                        {["f16", "q8_0", "q4_0", "f32", "bf16"].map((value) => (
                          <option key={value}>{value}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Slots
                      <input
                        type="number"
                        min="1"
                        max="32"
                        value={slots}
                        onChange={(e) => setSlots(Number(e.target.value))}
                      />
                    </label>
                  </div>
                  <label className="scope-toggle">
                    <input
                      type="checkbox"
                      checked={flashAttention}
                      onChange={(e) => setFlashAttention(e.target.checked)}
                    />
                    <span>
                      <b>Flash attention</b>
                    </span>
                  </label>
                  <label className="scope-toggle">
                    <input
                      type="checkbox"
                      checked={useMmap}
                      onChange={(e) => setUseMmap(e.target.checked)}
                    />
                    <span>
                      <b>Memory-mapped model</b>
                    </span>
                  </label>
                  <label className="text-field">
                    <span>Additional arguments</span>
                    <input
                      value={additionalArgs}
                      onChange={(e) => setAdditionalArgs(e.target.value)}
                      placeholder="--mlock"
                    />
                  </label>
                </fieldset>
              )}
              {pipelineMode !== "vlm" && (
                <fieldset className="tagger-picker">
                  <legend>Tagger-модели</legend>
                  {taggers.map((tagger) => {
                    const selected = taggerIds
                      .split(",")
                      .map((value) => value.trim())
                      .includes(tagger.id);
                    return (
                      <label key={tagger.id} className="scope-toggle">
                        <input
                          type="checkbox"
                          checked={selected}
                          disabled={!tagger.installed}
                          onChange={(e) => {
                            const values = new Set(
                              taggerIds
                                .split(",")
                                .map((value) => value.trim())
                                .filter(Boolean),
                            );
                            if (e.target.checked) values.add(tagger.id);
                            else values.delete(tagger.id);
                            setTaggerIds([...values].join(", "));
                          }}
                        />
                        <span>
                          <b>{tagger.name}</b>
                          <small>
                            {tagger.installed ? tagger.id : "Не установлен"}
                          </small>
                        </span>
                      </label>
                    );
                  })}
                  <div className="runtime-grid tagger-policy">
                    <label>
                      General threshold
                      <input
                        type="number"
                        min="0"
                        max="1"
                        step="0.01"
                        value={generalThreshold}
                        onChange={(e) =>
                          setGeneralThreshold(Number(e.target.value))
                        }
                      />
                    </label>
                    <label>
                      Character threshold
                      <input
                        type="number"
                        min="0"
                        max="1"
                        step="0.01"
                        value={characterThreshold}
                        onChange={(e) =>
                          setCharacterThreshold(Number(e.target.value))
                        }
                      />
                    </label>
                    <label>
                      Top K
                      <input
                        type="number"
                        min="1"
                        max="4096"
                        value={taggerTopK}
                        onChange={(e) => setTaggerTopK(Number(e.target.value))}
                      />
                    </label>
                  </div>
                  <label className="scope-toggle">
                    <input
                      type="checkbox"
                      checked={includeCharacters}
                      onChange={(e) => setIncludeCharacters(e.target.checked)}
                    />
                    <span>
                      <b>Include character tags</b>
                    </span>
                  </label>
                  <label className="scope-toggle">
                    <input
                      type="checkbox"
                      checked={includeRating}
                      onChange={(e) => setIncludeRating(e.target.checked)}
                    />
                    <span>
                      <b>Include rating tags</b>
                    </span>
                  </label>
                  <label className="text-field">
                    <span>Blacklist, comma separated</span>
                    <input
                      value={taggerBlacklist}
                      onChange={(e) => setTaggerBlacklist(e.target.value)}
                    />
                  </label>
                  <label className="text-field">
                    <span>Aliases, one source=target per line</span>
                    <textarea
                      value={taggerAliases}
                      onChange={(e) => setTaggerAliases(e.target.value)}
                    />
                  </label>
                </fieldset>
              )}
            </section>
          </div>
          <aside className="effective-config" id="expert-effective">
            <span>EFFECTIVE CONFIG</span>
            <dl>
              <dt>Данные</dt>
              <dd>{project.last_scan?.images ?? 0} images</dd>
              <dt>Scope</dt>
              <dd>{scope}</dd>
              <dt>Результат</dt>
              <dd>
                {pipelineMode === "tagger_only" ? "только теги" : resultType}
              </dd>
              <dt>Контекст</dt>
              <dd>{contextSize.toLocaleString("ru-RU")}</dd>
              <dt>Максимальный вывод</dt>
              <dd>{maxTokens.toLocaleString("ru-RU")}</dd>
              <dt>Рассуждение</dt>
              <dd>
                {analysisMode === "fast"
                  ? "выключено"
                  : budget.toLocaleString("ru-RU")}
              </dd>
              <dt>Параметры генерации</dt>
              <dd>
                T {temperature} · P {topP}
              </dd>
              <dt>Model lifecycle</dt>
              <dd>{keepAlive === 0 ? "unload" : `${keepAlive / 60} min`}</dd>
              {pipelineMode !== "tagger_only" && (
                <>
                  <dt>Среда выполнения</dt>
                  <dd>
                    {gpuLayers} GPU · {threads} потоков · {batchSize}/
                    {ubatchSize}
                  </dd>
                </>
              )}
              {pipelineMode !== "vlm" && (
                <>
                  <dt>Фильтр тегов</dt>
                  <dd>
                    {generalThreshold}/{characterThreshold} · top {taggerTopK}
                  </dd>
                </>
              )}
            </dl>
          </aside>
        </div>
        <button className="primary wide" disabled={busy}>
          {busy ? "Рассчитываю…" : "Построить экспертный план"}
        </button>
        {error && <p className="error">{error}</p>}
      </form>
    </main>
  );
}

function PrepareAuto({
  project,
  onBack,
  onExpert,
  onPlan,
}: {
  project: Project;
  onBack: () => void;
  onExpert: () => void;
  onPlan: (plan: AutoPlan, options: Record<string, unknown>) => void;
}) {
  const [scope, setScope] = useState("missing");
  const [detail, setDetail] = useState("balanced");
  const [reviewEach, setReviewEach] = useState(false);
  const [analysisMode, setAnalysisMode] = useState("fast");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    const options = {
      scope,
      detail,
      result_type: "hybrid_caption",
      focus: "balanced",
      language: "en",
      review_policy: reviewEach ? "stop_on_review" : "queue",
      analysis_mode: analysisMode,
    };
    try {
      onPlan(await api.autoPlan(project.id, options), options);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="overview narrow">
      <button className="back" onClick={onBack}>
        ← Обзор проекта
      </button>
      <ProgressRail current="prepare" />
      <span className="eyebrow">Auto</span>
      <h1 className="page-title">Какой результат подготовить?</h1>
      <p className="lead">
        Ответьте на несколько вопросов — сервис рассчитает безопасный план до
        запуска.
      </p>
      <form className="choice-card auto-form" onSubmit={submit}>
        <fieldset>
          <legend>Какие файлы обработать</legend>
          <label>
            <input
              type="radio"
              name="scope"
              checked={scope === "missing"}
              onChange={() => setScope("missing")}
            />
            <span>
              <b>Только без описаний</b>
              <small>Существующие captions останутся без изменений</small>
            </span>
          </label>
          <label>
            <input
              type="radio"
              name="scope"
              checked={scope === "all"}
              onChange={() => setScope("all")}
            />
            <span>
              <b>Все изображения</b>
              <small>Существующие captions будут перезаписаны</small>
            </span>
          </label>
        </fieldset>
        <fieldset>
          <legend>Анализ изображения</legend>
          <label>
            <input
              type="radio"
              name="analysis"
              checked={analysisMode === "fast"}
              onChange={() => setAnalysisMode("fast")}
            />
            <span>
              <b>Быстрее</b>
              <small>
                Без внутреннего reasoning — подходит для больших datasets
              </small>
            </span>
          </label>
          <label>
            <input
              type="radio"
              name="analysis"
              checked={analysisMode === "accurate"}
              onChange={() => setAnalysisMode("accurate")}
            />
            <span>
              <b>Точнее</b>
              <small>
                До 1024 токенов анализа перед caption; займёт больше времени
              </small>
            </span>
          </label>
        </fieldset>
        <fieldset>
          <legend>Как проверять результат</legend>
          <label>
            <input
              type="checkbox"
              checked={reviewEach}
              onChange={(event) => setReviewEach(event.target.checked)}
            />
            <span>
              <b>Проверять каждое описание</b>
              <small>
                После генерации откроется изображение и удобный редактор; режим
                можно отключить во время запуска
              </small>
            </span>
          </label>
        </fieldset>
        <fieldset>
          <legend>Уровень подробности</legend>
          <div className="segmented">
            {[
              ["concise", "Кратко"],
              ["balanced", "Сбалансированно"],
              ["detailed", "Подробно"],
            ].map(([value, label]) => (
              <button
                type="button"
                className={detail === value ? "selected" : ""}
                onClick={() => setDetail(value)}
                key={value}
              >
                {label}
              </button>
            ))}
          </div>
        </fieldset>
        <button className="primary wide" disabled={busy}>
          {busy ? "Строю план…" : "Построить рекомендуемый план"}
        </button>
      </form>
    </main>
  );
}

function RecommendedPlan({
  plan,
  onBack,
  onRun,
  onConfigured,
}: {
  plan: AutoPlan;
  onBack: () => void;
  onRun: (id: string) => void;
  onConfigured: () => Promise<void>;
}) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmOverwrite, setConfirmOverwrite] = useState(false);
  const [keepWarm, setKeepWarm] = useState(false);
  const [vlmPath, setVlmPath] = useState(
    String(plan.model_snapshot.model_path ?? ""),
  );
  const [mmprojPath, setMmprojPath] = useState(
    String(plan.model_snapshot.mmproj_path ?? ""),
  );
  const managed = plan.model_snapshot.backend_type === "managed_llama";
  async function pick(kind: "vlm" | "mmproj") {
    const picker = window.pywebview?.api?.select_gguf_file;
    if (!picker) {
      setMessage("Выбор модели доступен в оконной версии Tag Manager");
      return;
    }
    const selected = await picker(kind, kind === "vlm" ? vlmPath : mmprojPath);
    if (selected) {
      if (kind === "vlm") setVlmPath(selected);
      else setMmprojPath(selected);
    }
  }
  async function configure() {
    setBusy(true);
    setMessage("");
    try {
      await api.configureModels(vlmPath, mmprojPath);
      await onConfigured();
    } catch (reason) {
      setMessage(
        reason instanceof Error
          ? reason.message
          : "Не удалось настроить модель",
      );
    } finally {
      setBusy(false);
    }
  }
  async function test() {
    setBusy(true);
    setMessage("");
    try {
      const resources = {
        ...plan.effective_resource_configuration,
        keep_alive_seconds: keepWarm ? 180 : 0,
      };
      const response = await api.testRun(
        plan.project_id,
        plan.recipe_draft,
        plan.model_snapshot,
        resources,
      );
      if (response.state.run_id) onRun(response.state.run_id);
      else setMessage(response.state.blocker ?? "Пробный запуск недоступен");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Ошибка запуска");
    } finally {
      setBusy(false);
    }
  }
  async function startFull() {
    const destructive =
      plan.recipe_draft.tagger_write_mode !== "augment" &&
      plan.recipe_draft.caption_write_mode !== "vlm_augment";
    if (plan.scope.overwrite > 0 && destructive) {
      setConfirmOverwrite(true);
      return;
    }
    await executeFullRun();
  }
  async function executeFullRun() {
    setBusy(true);
    setMessage("");
    try {
      const created = await api.createRun(plan.project_id, plan);
      await api.startRun(created.state.run_id);
      onRun(created.state.run_id);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Ошибка запуска");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <>
        {confirmOverwrite && (
          <ConfirmDialog
            title="Запустить с перезаписью?"
            message={`Будут перезаписаны captions: ${plan.scope.overwrite}.`}
            detail="Проверьте область изменений перед запуском. Изменения затронут файлы dataset."
            destructive
            onCancel={() => setConfirmOverwrite(false)}
            onConfirm={() => {
              setConfirmOverwrite(false);
              void executeFullRun();
            }}
          />
        )}
      </>
      <main className="overview narrow">
        <button className="back" onClick={onBack}>
          ← Изменить параметры
        </button>
        <ProgressRail current="prepare" />
        <span className="eyebrow">Рекомендуемый план</span>
        <h1 className="page-title">
          {plan.blockers.length
            ? "Перед запуском нужна модель."
            : "Всё готово к пробному запуску."}
        </h1>
        <section className="plan-card">
          <div className="consequences">
            <div>
              <strong>{plan.scope.create}</strong>
              <span>будет создано</span>
            </div>
            <div>
              <strong>{plan.scope.overwrite}</strong>
              <span>
                {plan.recipe_draft.tagger_write_mode === "augment" ||
                plan.recipe_draft.caption_write_mode === "vlm_augment"
                  ? "будет дополнено"
                  : "будет перезаписано"}
              </span>
            </div>
            <div>
              <strong>{plan.scope.preserve}</strong>
              <span>без изменений</span>
            </div>
          </div>
          {plan.warnings.map((warning) => (
            <p className="warning" key={warning}>
              ⚠ {warning}
            </p>
          ))}
          {plan.blockers.map((blocker) => (
            <p className="model-blocker" key={blocker}>
              Требуется настройка · {blocker}
            </p>
          ))}
          {managed && (
            <section className="model-setup">
              <span className="eyebrow">Модель запуска</span>
              <div>
                <button type="button" onClick={() => pick("vlm")}>
                  <b>VLM-модель</b>
                  <small>{vlmPath || "Выбрать файл .gguf"}</small>
                </button>
                <button type="button" onClick={() => pick("mmproj")}>
                  <b>Vision projection</b>
                  <small>{mmprojPath || "Выбрать mmproj.gguf"}</small>
                </button>
              </div>
              {plan.blockers.length > 0 && (
                <button
                  className="primary"
                  disabled={busy || !vlmPath || !mmprojPath}
                  onClick={configure}
                >
                  {busy ? "Проверяю…" : "Сохранить и проверить модель"}
                </button>
              )}
            </section>
          )}
          <label className="keep-warm">
            <input
              type="checkbox"
              checked={keepWarm}
              onChange={(event) => setKeepWarm(event.target.checked)}
            />
            <span>
              <b>Оставить модель загруженной на 3 минуты</b>
              <small>
                Ускорит переход от проверки к полному запуску; память
                освободится автоматически
              </small>
            </span>
          </label>
          <details>
            <summary>Почему выбран этот план?</summary>
            <p>{plan.explanation.resources}</p>
            <p>{plan.explanation.tradeoff}</p>
            <p>{plan.explanation.model}</p>
          </details>
          <details className="plan-recipe-summary">
            <summary>Итоговая конфигурация и промпты</summary>
            <dl>
              {Object.entries(plan.recipe_draft)
                .filter(
                  ([key]) => !["system_prompt", "user_prompt"].includes(key),
                )
                .map(([key, value]) => (
                  <div key={key}>
                    <dt>{recipeLabels[key] ?? key}</dt>
                    <dd>
                      {Array.isArray(value)
                        ? value.join(", ") || "Не используются"
                        : String(value)}
                    </dd>
                  </div>
                ))}
            </dl>
            <h3>System prompt</h3>
            <pre>{String(plan.recipe_draft.system_prompt ?? "")}</pre>
            <h3>User prompt</h3>
            <pre>{String(plan.recipe_draft.user_prompt ?? "")}</pre>
          </details>
          <div className="plan-actions">
            <button
              className="primary"
              disabled={plan.blockers.length > 0 || busy}
              onClick={test}
            >
              {busy ? "Запускаю…" : "Проверить на 3 изображениях"}
            </button>
            <button
              className="ghost"
              disabled={busy || plan.blockers.length > 0}
              onClick={startFull}
            >
              Запустить весь dataset без пробы
            </button>
          </div>
          {message && (
            <p className="error" role="alert">
              {message}
            </p>
          )}
        </section>
      </main>
    </>
  );
}

const stageLabels: Record<string, string> = {
  discovery: "Проверяю файлы",
  model_preparation: "Готовлю модель",
  tagger: "Анализирую изображения",
  vlm: "Создаю описание",
  writing: "Сохраняю результат",
  unloading: "Освобождаю память",
  finished: "Обработка завершена",
};
const recipeLabels: Record<string, string> = {
  goal: "Задача",
  result_type: "Формат результата",
  language: "Язык",
  detail: "Подробность",
  trigger_word: "Trigger word",
  review_policy: "Проверка",
  temperature: "Temperature",
  max_tokens: "Максимум токенов",
  top_p: "Top P",
  context_size: "Размер контекста",
  auto_retry: "Автоповтор",
  disable_thinking: "Reasoning отключён",
  reasoning_budget: "Лимит reasoning",
  pipeline_tagger_ids: "Tagger-предобработка",
  include_subfolders: "Включая подпапки",
  pipeline_mode: "Pipeline",
  tagger_write_mode: "Запись тегов",
};
function CompletedRunSummary({
  run,
  onBack,
  onReview,
  onAdjust,
  onRetest,
  onStartFull,
}: {
  run: Run;
  onBack: () => void;
  onReview: () => void;
  onAdjust: () => void;
  onRetest: () => void;
  onStartFull: () => void;
}) {
  const [showResults, setShowResults] = useState(false);
  const m = run.inference_metrics;
  const s = run.summary;
  const results = Array.isArray(s.test_results)
    ? (s.test_results as Array<{
        image: string;
        caption: string;
        quality: string;
      }>)
    : [];
  const testDrive = Boolean(run.scope_plan.test_drive);
  const average =
    typeof s.average_tokens_per_second === "number"
      ? s.average_tokens_per_second
      : m.tokens_per_second;
  return (
    <main className="overview narrow">
      <button className="back" onClick={onBack}>
        ← Проект
      </button>
      <ProgressRail current={run.progress.review_count ? "review" : "run"} />
      <span className="eyebrow">
        {testDrive ? "Проверка на 3 изображениях" : "Итог запуска"}
      </span>
      <h1 className="page-title">{run.progress.done} обработано</h1>
      <section className="run-summary">
        <div>
          <strong>✓ {run.progress.done - run.progress.errors}</strong>
          <span>результатов</span>
        </div>
        <div className={run.progress.review_count ? "attention" : ""}>
          <strong>{run.progress.review_count}</strong>
          <span>требуют проверки</span>
        </div>
        <div className={run.progress.errors ? "attention" : ""}>
          <strong>{run.progress.errors}</strong>
          <span>проблем</span>
        </div>
      </section>
      <section className="summary-facts">
        <div>
          <span>Создано</span>
          <b>{String(s.created ?? (testDrive ? 0 : run.progress.done))}</b>
        </div>
        <div>
          <span>Перезаписано</span>
          <b>{String(s.overwritten ?? 0)}</b>
        </div>
        <div>
          <span>Сохранено без изменений</span>
          <b>{String(s.preserved ?? run.scope_plan.preserve ?? 0)}</b>
        </div>
        <div>
          <span>Длительность</span>
          <b>
            {typeof s.duration_seconds === "number"
              ? `${Math.round(s.duration_seconds)} сек`
              : "—"}
          </b>
        </div>
      </section>
      <p className="summary-speed">
        Средняя скорость{" "}
        <b>{average != null ? `${average.toFixed(1)} ток/с` : "не измерена"}</b>
        {typeof s.min_tokens_per_second === "number" &&
          typeof s.max_tokens_per_second === "number" && (
            <small>
              {" "}
              · диапазон {s.min_tokens_per_second.toFixed(1)}–
              {s.max_tokens_per_second.toFixed(1)}
            </small>
          )}
      </p>
      {showResults && (
        <section className="test-results">
          {results.map((result) => (
            <article key={result.image}>
              <img
                src={api.imageUrl(run.project_id, result.image)}
                alt={result.image}
              />
              <div>
                <span>{result.image}</span>
                <p>{result.caption}</p>
                {result.quality !== "ok" && <small>{result.quality}</small>}
              </div>
            </article>
          ))}
        </section>
      )}
      <div className="summary-actions">
        {results.length > 0 && (
          <button
            className="ghost secondary-action"
            onClick={() => setShowResults((value) => !value)}
          >
            {showResults ? "Скрыть результаты" : "Посмотреть результаты"}
          </button>
        )}
        {run.progress.review_count > 0 && (
          <button className="ghost secondary-action" onClick={onReview}>
            Очередь проверки
          </button>
        )}
        {testDrive && (
          <button className="ghost secondary-action" onClick={onAdjust}>
            Изменить настройки
          </button>
        )}
        {testDrive && (
          <button className="ghost secondary-action" onClick={onRetest}>
            Проверить ещё 3
          </button>
        )}
        <button
          className="primary primary-large"
          onClick={testDrive ? onStartFull : onAdjust}
        >
          {testDrive ? "Запустить весь dataset" : "Создать новый запуск"}
          <b>→</b>
        </button>
      </div>
      <details className="inference-details">
        <summary>Подробнее · Inference details</summary>
        <dl>
          <dt>Backend</dt>
          <dd>{m.backend ?? "Недоступно"}</dd>
          <dt>Модель</dt>
          <dd>{m.model ?? "Недоступно"}</dd>
          <dt>Токены последнего изображения</dt>
          <dd>{m.total_tokens?.toLocaleString("ru-RU") ?? "Недоступно"}</dd>
          <dt>Состояние модели</dt>
          <dd>
            {s.model_release === "keep_warm"
              ? "Оставлена в памяти на 3 минуты"
              : "Выгружена"}
          </dd>
        </dl>
      </details>
    </main>
  );
}
function RunCockpit({
  runId,
  onBack,
  onReview,
  onAdjust,
  onRetest,
  onStartFull,
  onRunChanged,
}: {
  runId: string;
  onBack: () => void;
  onReview: () => void;
  onAdjust: () => void;
  onRetest: () => void;
  onStartFull: () => void;
  onRunChanged: (runId: string) => void;
}) {
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState("");
  const [showResults, setShowResults] = useState(false);
  const [resuming, setResuming] = useState(false);
  const notified = useRef("");
  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const next = await api.run(runId);
        if (active) setRun(next);
      } catch (reason) {
        if (active)
          setError(reason instanceof Error ? reason.message : "Связь потеряна");
      }
    };
    poll();
    const timer = setInterval(poll, 1000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [runId]);
  useEffect(() => {
    if (run?.summary.awaiting_review_item_id) onReview();
  }, [run?.summary.awaiting_review_item_id, onReview]);
  useEffect(() => {
    if (
      !run ||
      notified.current === run.status ||
      !document.hidden ||
      !("Notification" in window) ||
      Notification.permission !== "granted"
    )
      return;
    if (
      ["completed", "failed", "stopped"].includes(run.status) ||
      run.summary.awaiting_review_item_id
    ) {
      notified.current = run.status;
      new Notification("Tag Manager", {
        body:
          run.status === "completed"
            ? "Обработка dataset завершена"
            : run.status === "failed"
              ? "Запуск завершился с ошибкой"
              : run.summary.awaiting_review_item_id
                ? "Caption ожидает проверки"
                : "Запуск безопасно остановлен",
      });
    }
  }, [run]);
  useEffect(() => {
    const hotkey = (event: KeyboardEvent) => {
      if (
        event.code !== "Space" ||
        event.ctrlKey ||
        event.altKey ||
        event.metaKey ||
        event.target instanceof HTMLInputElement ||
        event.target instanceof HTMLTextAreaElement ||
        !run ||
        ["completed", "stopped", "failed"].includes(run.status)
      )
        return;
      event.preventDefault();
      command(run.status === "paused" ? "resume" : "pause");
    };
    window.addEventListener("keydown", hotkey);
    return () => window.removeEventListener("keydown", hotkey);
  }, [run]);
  async function command(action: "pause" | "resume" | "stop") {
    try {
      setRun((await api.runCommand(runId, action)).state);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Команда не выполнена",
      );
    }
  }
  async function resumeRemaining() {
    setResuming(true);
    setError("");
    try {
      const next = (await api.resumeRemaining(runId)).state;
      setRun(next);
      onRunChanged(next.run_id);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Не удалось продолжить оставшиеся",
      );
    } finally {
      setResuming(false);
    }
  }
  if (!run)
    return (
      <main className="overview narrow">
        <p>{error || "Восстанавливаю состояние запуска…"}</p>
      </main>
    );
  const m = run.inference_metrics;
  const terminal = ["completed", "stopped", "failed"].includes(run.status);
  if (terminal && run.status === "completed")
    return (
      <CompletedRunSummary
        run={run}
        onBack={onBack}
        onReview={onReview}
        onAdjust={onAdjust}
        onRetest={onRetest}
        onStartFull={onStartFull}
      />
    );
  if (terminal && run.status === "completed") {
    const results = Array.isArray(run.summary.test_results)
      ? (run.summary.test_results as Array<{
          image: string;
          caption: string;
          quality: string;
        }>)
      : [];
    const testDrive = Boolean(run.scope_plan.test_drive);
    return (
      <main className="overview narrow">
        <button className="back" onClick={onBack}>
          ← Проект
        </button>
        <ProgressRail current={run.progress.review_count ? "review" : "run"} />
        <span className="eyebrow">
          {testDrive ? "Проверка на 3 изображениях" : "Итог запуска"}
        </span>
        <h1 className="page-title">
          {run.progress.done} изображения обработаны
        </h1>
        <section className="run-summary">
          <div>
            <strong>✓ {run.progress.done - run.progress.errors}</strong>
            <span>результата</span>
          </div>
          <div className={run.progress.review_count ? "attention" : ""}>
            <strong>{run.progress.review_count}</strong>
            <span>требуют проверки</span>
          </div>
          <div className={run.progress.errors ? "attention" : ""}>
            <strong>{run.progress.errors}</strong>
            <span>проблем</span>
          </div>
        </section>
        <p className="summary-speed">
          Средняя скорость{" "}
          <b>
            {m.tokens_per_second != null
              ? `${m.tokens_per_second.toFixed(1)} ток/с`
              : "не измерена"}
          </b>
        </p>
        {showResults && (
          <section className="test-results">
            {results.map((result) => (
              <article key={result.image}>
                <img
                  src={api.imageUrl(run.project_id, result.image)}
                  alt={result.image}
                />
                <div>
                  <span>{result.image}</span>
                  <p>{result.caption}</p>
                  {result.quality !== "ok" && <small>{result.quality}</small>}
                </div>
              </article>
            ))}
          </section>
        )}
        <div className="summary-actions">
          {results.length > 0 && (
            <button
              className="ghost secondary-action"
              onClick={() => setShowResults((value) => !value)}
            >
              {showResults ? "Скрыть результаты" : "Посмотреть результаты"}
            </button>
          )}
          {run.progress.review_count > 0 && (
            <button className="ghost secondary-action" onClick={onReview}>
              Очередь проверки
            </button>
          )}
          {testDrive && (
            <button className="ghost secondary-action" onClick={onAdjust}>
              Изменить настройки
            </button>
          )}
          {testDrive && (
            <button className="ghost secondary-action" onClick={onRetest}>
              Проверить ещё 3
            </button>
          )}
          <button
            className="primary primary-large"
            onClick={testDrive ? onStartFull : onAdjust}
          >
            {testDrive ? "Запустить весь dataset" : "Создать новый запуск"}
            <b>→</b>
          </button>
        </div>
        <details className="inference-details">
          <summary>Подробнее · Inference details</summary>
          <dl>
            <dt>Backend</dt>
            <dd>{m.backend ?? "Недоступно"}</dd>
            <dt>Модель</dt>
            <dd>{m.model ?? "Недоступно"}</dd>
            <dt>Токены</dt>
            <dd>{m.total_tokens?.toLocaleString("ru-RU") ?? "Недоступно"}</dd>
            <dt>Время</dt>
            <dd>
              {m.elapsed_seconds != null
                ? `${m.elapsed_seconds.toFixed(1)} сек`
                : "Недоступно"}
            </dd>
          </dl>
        </details>
      </main>
    );
  }
  if (terminal) {
    const failed = run.status === "failed";
    const remaining = Math.max(0, run.progress.total - run.progress.done);
    return (
      <main className="overview narrow">
        <button className="back" onClick={onBack}>
          ← Проект
        </button>
        <ProgressRail current="run" />
        <span className="eyebrow">
          {failed ? "Запуск прерван ошибкой" : "Безопасно остановлено"}
        </span>
        <h1 className="page-title">
          {run.progress.done} из {run.progress.total} сохранено
        </h1>
        <p className="lead">
          {remaining
            ? `Осталось обработать ${remaining}. Уже созданные captions не будут потеряны.`
            : "Все доступные результаты сохранены."}
        </p>
        {failed && (
          <p className="run-failure" role="alert">
            {String(run.summary.error ?? "Причина ошибки не записана")}
          </p>
        )}
        <section className="run-summary">
          <div>
            <strong>
              ✓ {Math.max(0, run.progress.done - run.progress.errors)}
            </strong>
            <span>сохранено</span>
          </div>
          <div>
            <strong>{remaining}</strong>
            <span>осталось</span>
          </div>
          <div className={run.progress.errors ? "attention" : ""}>
            <strong>{run.progress.errors}</strong>
            <span>ошибок</span>
          </div>
        </section>
        <div className="summary-actions">
          <button className="ghost secondary-action" onClick={onBack}>
            Вернуться в проект
          </button>
          <button
            className="primary primary-large"
            onClick={remaining ? resumeRemaining : onAdjust}
            disabled={resuming}
          >
            {resuming
              ? "Запускаю продолжение…"
              : remaining
                ? "Продолжить оставшиеся"
                : "Создать новый запуск"}
            <b>→</b>
          </button>
        </div>
      </main>
    );
  }
  return (
    <main className="overview narrow">
      <button className="back" onClick={onBack}>
        ← Проект
      </button>
      <ProgressRail current="run" />
      <span className="eyebrow">
        {run.status === "paused" ? "На паузе" : "Обработка продолжается в фоне"}
      </span>
      <h1 className="run-stage">{stageLabels[run.stage] ?? run.stage}</h1>
      <div className="run-progress">
        <strong>
          {run.progress.done} <i>/ {run.progress.total}</i>
        </strong>
        <span>{run.progress.current_image ?? "Ожидание первого файла"}</span>
      </div>
      <div className="live-metrics">
        <b>
          {m.tokens_per_second != null
            ? `${m.tokens_per_second.toFixed(1)} ток/с`
            : "Скорость —"}
        </b>
        <b>
          {m.total_tokens != null
            ? `${m.total_tokens.toLocaleString("ru-RU")} токенов`
            : "Токены —"}
        </b>
        <b>
          {m.item_elapsed_seconds != null
            ? `${m.item_elapsed_seconds.toFixed(1)} сек`
            : "Время —"}
        </b>
      </div>
      <div className="run-counts">
        <span>✓ {Math.max(0, run.progress.done - run.progress.errors)}</span>
        <span>{run.progress.review_count} требуют проверки</span>
        <span>{run.progress.errors} ошибок</span>
      </div>
      <div className="run-actions">
        {!terminal && (
          <button
            className="ghost"
            onClick={() =>
              command(run.status === "paused" ? "resume" : "pause")
            }
          >
            {run.status === "paused" ? "Продолжить" : "Пауза"}
          </button>
        )}
        {!terminal && (
          <button className="danger" onClick={() => command("stop")}>
            Остановить безопасно
          </button>
        )}
      </div>
      <details className="inference-details">
        <summary>Подробнее · Inference details</summary>
        <dl>
          <dt>Backend</dt>
          <dd>{m.backend ?? "Недоступно"}</dd>
          <dt>Модель</dt>
          <dd>{m.model ?? "Недоступно"}</dd>
          <dt>VRAM</dt>
          <dd>
            {m.vram_used_bytes == null
              ? "Недоступно"
              : `${(m.vram_used_bytes / 1073741824).toFixed(1)} ГБ`}
          </dd>
          <dt>RAM</dt>
          <dd>
            {m.ram_used_bytes == null
              ? "Недоступно"
              : `${(m.ram_used_bytes / 1073741824).toFixed(1)} ГБ`}
          </dd>
          <dt>Контекст</dt>
          <dd>
            {m.context_used_tokens == null
              ? "Недоступно"
              : `${m.context_used_tokens} / ${m.context_limit_tokens ?? "—"}`}
          </dd>
          <dt>Повторы</dt>
          <dd>{run.progress.retries}</dd>
        </dl>
      </details>
      {error && <p className="error">{error}</p>}
    </main>
  );
}

function ReviewQueue({
  project,
  onBack,
  activeRunId,
}: {
  project: Project;
  onBack: () => void;
  activeRunId?: string;
}) {
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [index, setIndex] = useState(0);
  const [caption, setCaption] = useState("");
  const [error, setError] = useState("");
  const visibleReviewId = useRef("");
  useEffect(() => {
    let mounted = true;
    const refresh = () =>
      api
        .reviews(project.id)
        .then((next) => {
          if (!mounted) return;
          const relevant = activeRunId
            ? next.filter((value) => value.run_id === activeRunId)
            : next;
          setItems(relevant);
          setIndex((value) =>
            Math.min(value, Math.max(0, relevant.length - 1)),
          );
          if (relevant[0]?.id !== visibleReviewId.current) {
            visibleReviewId.current = relevant[0]?.id ?? "";
            setCaption(relevant[0]?.proposed_caption ?? "");
          }
        })
        .catch((reason) => mounted && setError(reason.message));
    refresh();
    const timer = activeRunId ? setInterval(refresh, 800) : undefined;
    return () => {
      mounted = false;
      if (timer) clearInterval(timer);
    };
  }, [project.id, activeRunId]);
  async function continueWithoutReview(runId: string) {
    try {
      await api.disableReviewEach(runId);
      onBack();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Не удалось изменить режим",
      );
    }
  }
  const item = items[index];
  async function decide(action: "accept" | "edit" | "regenerate" | "skip") {
    if (!item) return;
    try {
      await api.reviewDecision(
        item.id,
        action,
        action === "edit" ? { caption } : {},
      );
      const next = items.filter((value) => value.id !== item.id);
      const nextIndex = Math.min(index, Math.max(0, next.length - 1));
      setItems(next);
      setIndex(nextIndex);
      setCaption(next[nextIndex]?.proposed_caption ?? "");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Решение не сохранено",
      );
    }
  }
  if (!item)
    return (
      <main className="overview narrow">
        <button className="back" onClick={onBack}>
          {activeRunId ? "← Текущий запуск" : "← Проект"}
        </button>
        <ProgressRail current="review" />
        <span className="eyebrow">Очередь проверки</span>
        <h1 className="page-title">Очередь проверки пуста</h1>
        <p className="lead">
          {activeRunId
            ? "Для текущего запуска пока нет элементов. Новые результаты появятся здесь автоматически."
            : "Нет изображений, ожидающих проверки."}
        </p>
        {activeRunId && (
          <button className="primary" onClick={onBack}>
            Вернуться к текущему запуску
          </button>
        )}
        {error && <p className="error">{error}</p>}
      </main>
    );
  return (
    <main className="overview narrow">
      <button className="back" onClick={onBack}>
        {activeRunId ? "← Текущий запуск" : "← Проект"}
      </button>
      <ProgressRail current="review" />
      <span className="eyebrow">
        Проверка · {index + 1} / {items.length}
      </span>
      <h1 className="page-title">Требуется ваше решение</h1>
      <div className="review-card">
        <img
          src={api.imageUrl(project.id, item.image_relative_path)}
          alt={item.image_relative_path}
        />
        <div className="review-reasons">
          {item.reasons.map((reason) => (
            <span key={reason}>{reason}</span>
          ))}
        </div>
        <textarea
          value={caption}
          onChange={(event) => setCaption(event.target.value)}
          aria-label="Описание изображения"
        />
        <div className="review-actions">
          <button
            className="primary"
            onClick={() =>
              decide(caption === item.proposed_caption ? "accept" : "edit")
            }
          >
            {caption === item.proposed_caption ? "Принять" : "Сохранить правку"}
          </button>
          <button className="ghost" onClick={() => decide("regenerate")}>
            Сгенерировать заново
          </button>
          <button className="ghost" onClick={() => decide("skip")}>
            Пропустить
          </button>
          {item.reason_codes.includes("manual_review") && (
            <button
              className="continue-without-review"
              onClick={() => continueWithoutReview(item.run_id)}
            >
              Продолжить без проверки
            </button>
          )}
        </div>
      </div>
      {error && <p className="error">{error}</p>}
    </main>
  );
}

export default function App() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [active, setActive] = useState<Project | null>(null);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [showDesktopSettings, setShowDesktopSettings] = useState(false);
  const [globalScreen, setGlobalScreen] = useState<"home" | "resources">(
    "home",
  );
  const savedScreen = localStorage.getItem("tag-manager.screen");
  const [screen, setScreen] = useState<
    | "overview"
    | "gallery"
    | "visual-search"
    | "health"
    | "new"
    | "prepare"
    | "expert"
    | "plan"
    | "run"
    | "review"
    | "history"
  >(
    savedScreen === "run" ||
      savedScreen === "review" ||
      savedScreen === "gallery" ||
      savedScreen === "visual-search" ||
      savedScreen === "health" ||
      savedScreen === "history" ||
      savedScreen === "plan" ||
      savedScreen === "new" ||
      savedScreen === "prepare" ||
      savedScreen === "expert"
      ? savedScreen
      : "overview",
  );
  const [plan, setPlan] = useState<AutoPlan | null>(null);
  const [visualReferences, setVisualReferences] = useState<string[]>([]);
  const [workflowMode, setWorkflowMode] = useState<"auto" | "expert">(
    localStorage.getItem("tag-manager.workflow-mode") === "expert"
      ? "expert"
      : "auto",
  );
  const [planOptions, setPlanOptions] = useState<Record<string, unknown>>({});
  const [runId, setRunId] = useState(
    localStorage.getItem("tag-manager.run-id") ?? "",
  );
  const [reviewOrigin, setReviewOrigin] = useState<"run" | "project">(
    localStorage.getItem("tag-manager.review-origin") === "run"
      ? "run"
      : "project",
  );
  const [overwriteConfirm, setOverwriteConfirm] = useState(false);
  const [runOrigin, setRunOrigin] = useState<"overview" | "history">(
    localStorage.getItem("tag-manager.run-origin") === "history"
      ? "history"
      : "overview",
  );

  useEffect(() => {
    Promise.all([api.status(), api.projects()])
      .then(([nextStatus, nextProjects]) => {
        setStatus(nextStatus);
        setProjects(nextProjects);
        const savedProjectId = localStorage.getItem("tag-manager.project-id");
        const savedProject = nextProjects.find(
          (project) => project.id === savedProjectId,
        );
        if (savedProject)
          api
            .scanProject(savedProject.id)
            .then(setActive)
            .catch(() => setActive(savedProject));
      })
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setBusy(false));
  }, []);

  useEffect(() => {
    if (active) localStorage.setItem("tag-manager.project-id", active.id);
  }, [active]);
  useEffect(() => {
    localStorage.setItem("tag-manager.screen", screen);
    if (runId) localStorage.setItem("tag-manager.run-id", runId);
  }, [screen, runId]);
  useEffect(
    () => localStorage.setItem("tag-manager.workflow-mode", workflowMode),
    [workflowMode],
  );
  useEffect(
    () => localStorage.setItem("tag-manager.review-origin", reviewOrigin),
    [reviewOrigin],
  );
  useEffect(
    () => localStorage.setItem("tag-manager.run-origin", runOrigin),
    [runOrigin],
  );

  useEffect(() => {
    if (!active) return;
    if (screen === "plan" && !plan)
      setScreen(workflowMode === "expert" ? "expert" : "prepare");
    if (screen === "run" && !runId) setScreen("overview");
    if (screen === "review" && reviewOrigin === "run" && !runId) {
      setReviewOrigin("project");
      setScreen("overview");
    }
  }, [active, plan, reviewOrigin, runId, screen, workflowMode]);

  function navigateProject(section: ProjectSection) {
    if (section === "new") {
      setScreen("new");
      return;
    }
    if (section === "review") setReviewOrigin("project");
    setScreen(section);
  }
  function closeProject() {
    localStorage.removeItem("tag-manager.project-id");
    localStorage.removeItem("tag-manager.screen");
    localStorage.removeItem("tag-manager.run-id");
    setGlobalScreen("home");
    setActive(null);
    setScreen("overview");
    setRunId("");
    setPlan(null);
  }
  function shell(section: ProjectSection, content: ReactNode) {
    return active ? (
      <ProjectShell
        project={active}
        section={section}
        onNavigate={navigateProject}
        onHome={closeProject}
        onResources={() => setGlobalScreen("resources")}
      >
        {content}
      </ProjectShell>
    ) : (
      content
    );
  }

  async function open(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      setActive(await api.openProject(path));
      setScreen("overview");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Не удалось открыть dataset",
      );
    } finally {
      setBusy(false);
    }
  }
  async function chooseFolder() {
    const picker = window.pywebview?.api?.select_dataset_folder;
    if (!picker) {
      setError("Системный выбор папки доступен в оконной версии Tag Manager");
      return;
    }
    const selected = await picker(path);
    if (selected) setPath(selected);
  }
  async function selectProject(
    project: Project,
    target: "overview" | "gallery" | "health" = "overview",
  ) {
    setBusy(true);
    setError("");
    try {
      setActive(await api.scanProject(project.id));
      setScreen(target);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Не удалось обновить dataset",
      );
    } finally {
      setBusy(false);
    }
  }
  async function retestPlan() {
    if (!plan) {
      setScreen("prepare");
      return;
    }
    const response = await api.testRun(
      plan.project_id,
      plan.recipe_draft,
      plan.model_snapshot,
      plan.effective_resource_configuration,
    );
    if (response.state.run_id) {
      setRunId(response.state.run_id);
      setScreen("run");
    }
  }
  async function startPlanFull() {
    if (!plan) {
      setScreen("prepare");
      return;
    }
    if (
      plan.scope.overwrite > 0 &&
      plan.recipe_draft.tagger_write_mode !== "augment" &&
      plan.recipe_draft.caption_write_mode !== "vlm_augment"
    ) {
      setOverwriteConfirm(true);
      return;
    }
    await executePlanFull();
  }
  async function executePlanFull() {
    if (!plan) return;
    const created = await api.createRun(plan.project_id, plan);
    await api.startRun(created.state.run_id);
    setRunId(created.state.run_id);
    setScreen("run");
  }
  async function adjustAfterRun() {
    if (active) {
      try {
        setActive(await api.scanProject(active.id));
      } catch {
        /* Planning will surface a durable project error. */
      }
    }
    setPlan(null);
    setScreen(workflowMode === "expert" ? "expert" : "prepare");
  }

  if (globalScreen === "resources")
    return (
      <ResourceWorkspace
        onBack={() => {
          if (active) setGlobalScreen("home");
          else setGlobalScreen("home");
        }}
      />
    );

  if (active && screen === "new")
    return shell(
      "new",
      <NewRunChooser
        project={active}
        onBack={() => setScreen("overview")}
        onAuto={() => {
          setWorkflowMode("auto");
          setScreen("prepare");
        }}
        onExpert={() => {
          setWorkflowMode("expert");
          setScreen("expert");
        }}
      />,
    );

  if (active && screen === "prepare")
    return shell(
      "prepare",
      <PrepareAuto
        project={active}
        onBack={() => setScreen("overview")}
        onExpert={() => {
          setScreen("expert");
        }}
        onPlan={(next, options) => {
          setWorkflowMode("auto");
          setPlan(next);
          setPlanOptions(options);
          setScreen("plan");
        }}
      />,
    );
  if (active && screen === "expert")
    return shell(
      "expert",
      <ExpertPrepare
        project={active}
        onAuto={() => {
          setScreen("prepare");
        }}
        onBack={() => {
          setScreen("overview");
        }}
        onPlan={(next, options) => {
          setWorkflowMode("expert");
          setPlan(next);
          setPlanOptions(options);
          setScreen("plan");
        }}
      />,
    );
  if (active && screen === "run" && runId)
    return shell(
      runOrigin === "history" ? "history" : "overview",
      <>
        {overwriteConfirm && (
          <ConfirmDialog
            title="Запустить с перезаписью?"
            message={`Будут перезаписаны captions: ${plan?.scope.overwrite ?? 0}.`}
            detail="Проверьте область изменений перед запуском."
            destructive
            onCancel={() => setOverwriteConfirm(false)}
            onConfirm={() => {
              setOverwriteConfirm(false);
              void executePlanFull();
            }}
          />
        )}
        <RunCockpit
          runId={runId}
          onBack={() => setScreen(runOrigin)}
          onReview={() => {
            setReviewOrigin("run");
            setScreen("review");
          }}
          onAdjust={adjustAfterRun}
          onRetest={retestPlan}
          onStartFull={startPlanFull}
          onRunChanged={setRunId}
        />
      </>,
    );
  if (active && screen === "review")
    return shell(
      "review",
      <ReviewQueue
        project={active}
        activeRunId={reviewOrigin === "run" ? runId || undefined : undefined}
        onBack={() => setScreen(reviewOrigin === "run" ? "run" : "overview")}
      />,
    );
  if (active && screen === "plan" && plan)
    return shell(
      "new",
      <RecommendedPlan
        plan={plan}
        onBack={() =>
          setScreen(workflowMode === "expert" ? "expert" : "prepare")
        }
        onRun={(id) => {
          setRunId(id);
          setRunOrigin("overview");
          setScreen("run");
        }}
        onConfigured={async () =>
          setPlan(await api.autoPlan(active.id, planOptions))
        }
      />,
    );
  if (active && screen === "gallery")
    return shell(
      "gallery",
      <ProjectGallery
        project={active}
        onBack={() => setScreen("overview")}
        onRun={(id) => {
          setRunId(id);
          setRunOrigin("overview");
          setWorkflowMode("auto");
          setScreen("run");
        }}
        onFindSimilar={(path) => { setVisualReferences([path]); setScreen("visual-search"); }}
      />,
    );
  if (active && screen === "visual-search")
    return shell(
      "visual-search",
      <VisualSearch
        project={active}
        initialReferences={visualReferences}
        onRun={(id) => {
          setRunId(id);
          setRunOrigin("overview");
          setWorkflowMode("auto");
          setScreen("run");
        }}
      />,
    );
  if (active && screen === "health")
    return shell(
      "health",
      <ProjectHealth
        project={active}
        onOpenGallery={() => setScreen("gallery")}
        onCreateCaptions={() => {
          setWorkflowMode("auto");
          setScreen("prepare");
        }}
      />,
    );
  if (active && screen === "history")
    return shell(
      "history",
      <ProjectHistory
        project={active}
        onOpenRun={(id) => {
          setRunId(id);
          setRunOrigin("history");
          setScreen("run");
        }}
      />,
    );
  if (active)
    return shell(
      "overview",
      <ProjectOverview
        project={active}
        onClose={closeProject}
        onScan={async (recursive) =>
          setActive(await api.scanProject(active.id, recursive))
        }
        onPrepare={() => setScreen("new")}
        onExpert={() => setScreen("new")}
        onGallery={() => setScreen("gallery")}
        onHealth={() => setScreen("health")}
        onReview={() => {
          setReviewOrigin("project");
          setScreen("review");
        }}
        onOpenRun={(id) => {
          setRunId(id);
          setRunOrigin("overview");
          setScreen("run");
        }}
      />,
    );
  return (
    <main className="home">
      {showDesktopSettings && (
        <DesktopSettingsPanel onClose={() => setShowDesktopSettings(false)} />
      )}
      <header>
        <BrandMark />
        <StatusBadge
          state={status?.status ?? "offline"}
          label={
            status?.status === "ready" ? "Сервис готов" : "Сервис недоступен"
          }
        />
        <button
          className="system-settings-button"
          title="Настройки приложения"
          aria-label="Настройки приложения"
          onClick={() => setShowDesktopSettings(true)}
        >
          ⚙
        </button>
        <button
          className="global-resource-button"
          onClick={() => setGlobalScreen("resources")}
        >
          Ресурсы
        </button>
      </header>
      <section className="welcome">
        <span className="eyebrow">Рабочее пространство dataset</span>
        <h1>Откройте dataset и продолжите работу.</h1>
        <p>
          Проекты, планы и очередь проверки сохраняются рядом с вашими
          изображениями.
        </p>
      </section>
      <form className="open-card" onSubmit={open}>
        <label htmlFor="dataset">Папка dataset</label>
        <div className="open-row">
          <input
            id="dataset"
            value={path}
            onChange={(event) => setPath(event.target.value)}
            placeholder="X:\Datasets\characters"
            required
          />
          <button
            type="button"
            className="browse-button"
            onClick={chooseFolder}
          >
            Выбрать папку
          </button>
          <button className="primary" disabled={busy}>
            {busy ? "Открываю…" : "Открыть проект"}
          </button>
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </form>
      <section className="recent">
        <div className="section-title">
          <h2>Недавние проекты</h2>
          <span>{projects.length}</span>
        </div>
        <div className="project-list">
          {projects.map((project) => (
            <article
              className="project-row"
              key={project.id}
              onClick={() => selectProject(project)}
              tabIndex={0}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  selectProject(project);
                }
              }}
            >
              <button
                className="project-row-main"
                onClick={(event) => {
                  event.stopPropagation();
                  selectProject(project);
                }}
              >
                <div>
                  <strong>{project.name}</strong>
                  <span>{project.dataset_path}</span>
                </div>
                <div className="project-meta">
                  <span>{project.last_scan?.images ?? 0} файлов</span>
                  <b>
                    {project.last_scan?.missing_captions
                      ? `${project.last_scan.missing_captions} требуют внимания`
                      : "Готов"}
                  </b>
                </div>
              </button>
              <div
                className="project-row-actions"
                aria-label={`Действия для ${project.name}`}
              >
                <button
                  onClick={(event) => {
                    event.stopPropagation();
                    selectProject(project, "gallery");
                  }}
                >
                  Галерея
                </button>
                <button
                  onClick={(event) => {
                    event.stopPropagation();
                    selectProject(project, "health");
                  }}
                >
                  Здоровье
                </button>
              </div>
            </article>
          ))}
          {!busy && projects.length === 0 && (
            <div className="empty">
              Здесь появятся недавно открытые datasets.
            </div>
          )}
        </div>
      </section>
    </main>
  );
}
