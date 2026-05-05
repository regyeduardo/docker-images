/**
 * app.js — Aplicação Frontend dos Agentes de Análise de Conteúdo.
 *
 * Módulos:
 * - AppState: gerenciamento de estado global
 * - UIManager: manipulação de DOM (loading, modals, dropdowns)
 * - ApiClient: chamadas fetch ao backend
 * - MermaidRenderer: sanitização e renderização de Mermaid
 * - ExamManager: lógica de navegação, respostas, resultado da prova
 * - ImportExportManager: lógica de import/export
 */

// =============================================================================
// Utilitários
// =============================================================================

/**
 * Garante que mudanças no DOM sejam pintadas pelo navegador antes de prosseguir.
 *
 * Fluxo:
 * 1. Forced reflow síncrono (leitura de offsetHeight) — força o navegador a
 *    recalcular layout imediatamente, aplicando classes como `display: flex`.
 * 2. Double requestAnimationFrame — aguarda DOIS ciclos de paint do navegador,
 *    garantindo que a pintura com o estado visual já ocorreu.
 *
 * Use SEMPRE após alterações visuais (ex: showLoading) que precedem
 * operações assíncronas longas (fetch, processamento).
 *
 * @returns {Promise<void>}
 */
function forcePaint() {
  // Forced reflow síncrono: browser recalcula layout AGORA
  void document.body.offsetHeight;
  // Double RAF: aguarda 2 ciclos de paint
  return new Promise((resolve) =>
    requestAnimationFrame(() => requestAnimationFrame(resolve))
  );
}

// =============================================================================
// AppState — Estado Global
// =============================================================================

class AppState {
  constructor() {
    this.generatedMarkdown = "";
    this.questionsData = null;
    this.userAnswers = {};
    this.currentQuestionIndex = 0;
  }

  /** Reseta todo o estado (usado após import). */
  reset() {
    this.generatedMarkdown = "";
    this.questionsData = null;
    this.userAnswers = {};
    this.currentQuestionIndex = 0;
  }

  /** Atualiza o markdown e reseta dados de questões. */
  setMarkdown(markdown) {
    this.generatedMarkdown = markdown;
    this.questionsData = null;
    this.userAnswers = {};
    this.currentQuestionIndex = 0;
  }

  /** Atualiza markdown e questões (usado após import de ZIP). */
  setMarkdownAndQuestions(markdown, questions) {
    this.generatedMarkdown = markdown;
    this.questionsData = questions || null;
    this.userAnswers = {};
    this.currentQuestionIndex = 0;
  }
}

// =============================================================================
// UIManager — Manipulação de DOM
// =============================================================================

class UIManager {
  constructor() {
    this.output = document.getElementById("output");
    this.optionsDropdown = document.getElementById("optionsDropdown");
    this.loadingOverlay = document.getElementById("loadingOverlay");
    this.loadingMessage = document.getElementById("loadingMessage");
    this.loadingSubMessage = document.getElementById("loadingSubMessage");
    this.processBtn = document.getElementById("processBtn");
  }

  /** Exibe o overlay de carregamento full-screen. */
  showLoading(message, subMessage) {
    if (!this.loadingOverlay) {
      console.warn("[Loading] Elemento loadingOverlay não encontrado no DOM");
      return;
    }
    if (this.loadingMessage)
      this.loadingMessage.textContent = message || "Processando...";
    if (this.loadingSubMessage)
      this.loadingSubMessage.textContent =
        subMessage || "Aguarde, pode levar vários minutos.";

    // Remove atributo hidden (native browser hiding)
    this.loadingOverlay.removeAttribute("hidden");

    // Aplica estilos inline para full-screen overlay centrado.
    // Usamos estilos inline (em vez de classes CSS) para garantir que
    // nenhum conflito de especificidade ou ordem de carregamento de CSS
    // impeça a exibição correta do overlay.
    const s = this.loadingOverlay.style;
    s.display = "flex";
    s.position = "fixed";
    s.top = "0";
    s.left = "0";
    s.width = "100%";
    s.height = "100%";
    s.background = "rgba(0, 0, 0, 0.7)";
    s.alignItems = "center";
    s.justifyContent = "center";
    s.zIndex = "9999";
    s.backdropFilter = "blur(4px)";
    s.webkitBackdropFilter = "blur(4px)";
  }

  /** Oculta o overlay de carregamento. */
  hideLoading() {
    if (!this.loadingOverlay) {
      console.warn("[Loading] Elemento loadingOverlay não encontrado no DOM");
      return;
    }
    // Esconde via inline style + atributo hidden (proteção dupla)
    this.loadingOverlay.style.display = "none";
    this.loadingOverlay.setAttribute("hidden", "");
  }

  /** Renderiza markdown no output e mostra o dropdown de opções. */
  renderMarkdown(markdown) {
    this.output.innerHTML = marked.parse(markdown);
    this.optionsDropdown.classList.remove("d-none");
  }

  /** Exibe mensagem de erro no output. */
  showError(message) {
    this.output.innerHTML = `<div class="alert alert-danger">${this._escapeHtml(message)}</div>`;
  }

  /** Exibe mensagem de aviso no output. */
  showWarning(message) {
    this.output.innerHTML = `<div class="alert alert-warning">${this._escapeHtml(message)}</div>`;
  }

  /** Escapa HTML para evitar XSS. */
  _escapeHtml(text) {
    const div = document.createElement("div");
    div.appendChild(document.createTextNode(text));
    return div.innerHTML;
  }
}

// =============================================================================
// ApiClient — Chamadas Fetch ao Backend
// =============================================================================

class ApiClient {
  /**
   * Processa conteúdo (upload ou YouTube).
   * @param {FormData} formData
   * @returns {Promise<Object>}
   */
  async processContent(formData) {
    const res = await fetch("/api/process", { method: "POST", body: formData });
    const text = await res.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch (parseErr) {
      console.error("Resposta não-JSON do /api/process:", text.slice(0, 1000));
      throw new Error(
        "Erro interno do servidor. Resposta inválida recebida.\n" +
          (res.ok ? "" : ` (HTTP ${res.status})`)
      );
    }
    if (data.error) throw new Error(data.error);
    return data;
  }

  /**
   * Gera questões a partir do markdown.
   * @param {string} markdown
   * @param {string} title
   * @returns {Promise<Object>}
   */
  async generateQuestions(markdown, title) {
    const res = await fetch("/api/questions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ markdown, title }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    return data;
  }

  /**
   * Importa arquivo .md ou .zip.
   * @param {File} file
   * @returns {Promise<Object>}
   */
  async importFile(file) {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/api/import", { method: "POST", body: formData });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    return data;
  }

  /**
   * Exporta ZIP com markdown e opcionalmente questões.
   * @param {string} markdown
   * @param {Object|null} questions
   * @param {string} filename
   * @returns {Promise<Blob>}
   */
  async exportZip(markdown, questions, filename) {
    const res = await fetch("/api/export-zip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ markdown, questions, filename }),
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.error || `Erro HTTP ${res.status}`);
    }
    return res.blob();
  }
}

// =============================================================================
// MermaidRenderer — Sanitização e Renderização de Mermaid
//
// CORRIGIDO EM 2026-05-05 (v4):
// - PASSO 0: \\n fora de labels → newline real (não espaço), evita concatenar linhas
// - PASSO 5a/5b/5c: aspas desbalanceadas assimétricas [""texto"] e ["texto""] → ["texto"]
// - Regex de character classes corrigidos (escapamentos incorretos)
// - Pipe duplo ||...|| → pipe único |...| no Passo 4 e aggressiveSanitize
// - Ordem de operações: valida parse ANTES de substituir DOM
// - Mensagem de erro inclui motivo (errorMsg)
// - Método _createFallback extraído para reuso
// =============================================================================

class MermaidRenderer {
  /**
   * Sanitizes Mermaid diagram code to fix common syntax errors from LLM output.
   *
   * Problemas corrigidos:
   * - \\n literal (backslash + n) fora de labels → quebra de linha real
   * - \\n literal dentro de labels → <br/>
   * - Labels com caracteres especiais sem aspas → adiciona aspas
   * - Aspas duplicadas simétricas [""texto""] → ["texto"]
   * - Aspas desbalanceadas assimétricas [""texto"] e ["texto""] → ["texto"]
   * - Labels com espaços sem aspas → adiciona aspas
   * - Setas |...| com caracteres especiais sem aspas → adiciona aspas
   */
  sanitizeMermaidCode(code) {
    if (!code) return code;
    let s = code;

    // Character class para detectar caracteres especiais em labels Mermaid.
    // CORRIGIDO: sem escapes desnecessários dentro de [...] do JS.
    // Regras: ] deve vir primeiro (ou ser escapada), - deve vir no fim.
    const SPECIAL = '[(){}[\\];,:?+*!@#$%&/=<>~`^\\-]';

    // PASSO 0: Substitui \n literal (escape sequence) por quebras de linha reais.
    // \n dentro de labels (após ", ], }) vira <br/>.
    // \n FORA de labels (separadores de linha Mermaid) vira newline real,
    // NÃO espaço — para evitar concatenar linhas distintas.
    // Character class corrigido: ["\]}]  (sem escapes redundantes)
    s = s.replace(/(["\]}])\s*\\n\s*/g, "$1<br/>");
    s = s.replace(/\s*\\n\s*/g, "\n");

    // PASSO 1: Remove quebras de linha dentro de labels [...] e {...}
    s = s.replace(/\[([^\]]*)\]/g, (match, label) => {
      return "[" + label.replace(/\s+/g, " ").trim() + "]";
    });
    s = s.replace(/\{([^}]*)\}/g, (match, label) => {
      return "{" + label.replace(/\s+/g, " ").trim() + "}";
    });

    // PASSO 2: Envolve labels em ID[label] com aspas se contiverem caracteres especiais
    // [^\]\n] impede match cross-line (labels Mermaid são single-line)
    const bracketRE = new RegExp(
      '([A-Za-z0-9_]+)\\[([^\\]\\n]*?' + SPECIAL + '[^\\]\\n]*?)\\]', 'g'
    );
    s = s.replace(bracketRE, (match, id, label) => {
      if (/^".*"$/.test(label.trim())) return match;
      return `${id}["${label}"]`;
    });

    // PASSO 3: Envolve labels em ID{label} (losango/decisão) com aspas
    // [^\}\n] impede match cross-line
    const curlyRE = new RegExp(
      '([A-Za-z0-9_]+)\\{([^\\}\\n]*?' + SPECIAL + '[^\\}\\n]*?)\\}', 'g'
    );
    s = s.replace(curlyRE, (match, id, label) => {
      if (/^".*"$/.test(label.trim())) return match;
      return `${id}{"${label}"}`;
    });

    // PASSO 4: Envolve texto em setas -->|texto| com aspas
    // CORRIGIDO: [^\|\n] impede match cross-line (causava |"Sim"|" C)
    const pipeRE = new RegExp(
      '\\|([^\\|\\n]*?' + SPECIAL + '[^\\|\\n]*?)\\|', 'g'
    );
    s = s.replace(pipeRE, (match, text) => {
      if (/^".*"$/.test(text.trim())) return match;
      return `|"${text}"|`;
    });

    // PASSO 5: Corrige aspas duplicadas/desbalanceadas em labels
    // [^"\n] impede match cross-line
    // 5a: aspas duplas simétricas [""texto""] -> ["texto"]
    s = s.replace(/([A-Za-z0-9_]+)\[""([^"\n]*)""\]/g, '$1["$2"]');
    s = s.replace(/([A-Za-z0-9_]+)\{""([^"\n]*)""\}/g, '$1{"$2"}');
    // 5b: aspas duplas só na abertura [""texto"] -> ["texto"]
    s = s.replace(/([A-Za-z0-9_]+)\[""([^"\n]*)"\]/g, '$1["$2"]');
    s = s.replace(/([A-Za-z0-9_]+)\{""([^"\n]*)"\}/g, '$1{"$2"}');
    // 5c: aspas duplas só no fechamento ["texto""] -> ["texto"]
    s = s.replace(/([A-Za-z0-9_]+)\["([^"\n]*?)""\]/g, '$1["$2"]');
    s = s.replace(/([A-Za-z0-9_]+)\{"([^"\n]*?)""\}/g, '$1{"$2"}');

    // PASSO 6: Garante que labels com espaços também usem aspas
    // [^\]\n] e [^\}\n] impedem match cross-line
    const spaceBracketRE = new RegExp(
      '([A-Za-z0-9_]+)\\[([A-Za-z0-9_\\u00C0-\\u024F]+\\s+[^\\]\\n]*?)\\]', 'g'
    );
    s = s.replace(spaceBracketRE, (match, id, label) => {
      if (/^".*"$/.test(label.trim())) return match;
      return `${id}["${label}"]`;
    });
    const spaceCurlyRE = new RegExp(
      '([A-Za-z0-9_]+)\\{([A-Za-z0-9_\\u00C0-\\u024F]+\\s+[^\\}\\n]*?)\\}', 'g'
    );
    s = s.replace(spaceCurlyRE, (match, id, label) => {
      if (/^".*"$/.test(label.trim())) return match;
      return `${id}{"${label}"}`;
    });

    return s;
  }

  /**
   * Sanitização agressiva para diagramas que ainda falham.
   * Envolve TODO label em aspas (último recurso).
   */
  aggressiveSanitize(code) {
    if (!code) return code;
    let s = code;

    // Remove \n literal e caracteres de controle
    // \n dentro de labels vira <br/>; \n fora de labels vira newline real
    s = s.replace(/(["\]}])\s*\\n\s*/g, "$1<br/>");
    s = s.replace(/\s*\\n\s*/g, "\n");
    s = s.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F]/g, "");

    // Envolve TODO label em [...] com aspas
    // [^\]\n] impede match cross-line
    s = s.replace(/([A-Za-z0-9_]+)\[([^\]\n]*?)\]/g, (match, id, label) => {
      const trimmed = label.trim();
      if (/^".*"$/.test(trimmed)) return match;
      return `${id}["${trimmed}"]`;
    });

    // Envolve TODO label em {...} com aspas
    // [^\}\n] impede match cross-line
    s = s.replace(/([A-Za-z0-9_]+)\{([^\}\n]*?)\}/g, (match, id, label) => {
      const trimmed = label.trim();
      if (/^".*"$/.test(trimmed)) return match;
      return `${id}{"${trimmed}"}`;
    });

    // Envolve TODO texto em |...| com aspas
    // [^\|\n] impede match cross-line
    s = s.replace(/\|([^\|\n]*?)\|/g, (match, text) => {
      const trimmed = text.trim();
      if (/^".*"$/.test(trimmed)) return match;
      return `|"${trimmed}"|`;
    });

    // Corrige aspas duplicadas/desbalanceadas
    // [^"\n] impede match cross-line
    // Simétricas [""texto""] -> ["texto"]
    s = s.replace(/([A-Za-z0-9_]+)\[""([^"\n]*)""\]/g, '$1["$2"]');
    s = s.replace(/([A-Za-z0-9_]+)\{""([^"\n]*)""\}/g, '$1{"$2"}');
    // Assimétricas: abertura [""texto"] -> ["texto"]
    s = s.replace(/([A-Za-z0-9_]+)\[""([^"\n]*)"\]/g, '$1["$2"]');
    s = s.replace(/([A-Za-z0-9_]+)\{""([^"\n]*)"\}/g, '$1{"$2"}');
    // Assimétricas: fechamento ["texto""] -> ["texto"]
    s = s.replace(/([A-Za-z0-9_]+)\["([^"\n]*?)""\]/g, '$1["$2"]');
    s = s.replace(/([A-Za-z0-9_]+)\{"([^"\n]*?)""\}/g, '$1{"$2"}');

    return s;
  }

  /**
   * Cria elemento de fallback para diagrama com erro.
   * @param {Element} originalEl - Elemento original (pre ou div) a ser substituído
   * @param {string} originalCode - Código Mermaid original (não sanitizado)
   * @param {string} [errorMsg] - Mensagem de erro opcional do Mermaid
   * @param {Element} container - Container pai onde inserir o fallback
   * @returns {Element} Elemento fallback criado
   */
  _createFallback(originalEl, originalCode, errorMsg, container) {
    const fallback = document.createElement("pre");
    fallback.className = "mermaid-fallback";
    let html = `<code>${this._escapeHtml(originalCode)}</code>`;
    html += `<br><small class="text-danger">⚠️ Erro ao renderizar diagrama</small>`;
    if (errorMsg) {
      html += `<br><small class="text-muted" style="font-size:0.75rem;">Motivo: ${this._escapeHtml(errorMsg)}</small>`;
    }
    fallback.innerHTML = html;

    if (originalEl && originalEl.parentNode) {
      originalEl.parentNode.replaceChild(fallback, originalEl);
    } else if (container) {
      container.appendChild(fallback);
    }
    return fallback;
  }

  /**
   * Renderiza todos os diagramas Mermaid dentro de um container.
   *
   * Fluxo corrigido:
   * 1. Extrai código original do <pre><code class="language-mermaid">
   * 2. Sanitiza o código
   * 3. Valida com mermaid.parse() — se falhar, tenta sanitização agressiva
   * 4. Só SUBSTITUI o DOM se o parse for bem-sucedido
   * 5. Tenta mermaid.run() — se falhar, mostra fallback
   */
  async renderDiagrams(container) {
    if (typeof mermaid === "undefined") return;
    const diagrams = container.querySelectorAll(".language-mermaid");
    console.log(
      `[Mermaid] Encontrados ${diagrams.length} diagramas para renderizar`
    );
    let successCount = 0;
    let hasNewlineWarning = false;
    let failCount = 0;

    for (const el of diagrams) {
      const pre = el.closest("pre");
      if (!pre) continue;
      const originalCode = el.textContent.trim();
      if (!originalCode) continue;

      if (/\\n/.test(originalCode)) {
        if (!hasNewlineWarning) {
          console.warn(
            "[Mermaid] Diagrama contém \\n literal — será substituído por <br/>"
          );
          hasNewlineWarning = true;
        }
      }

      // --- Tentativa 1: Sanitização normal ---
      let sanitizedCode = this.sanitizeMermaidCode(originalCode);
      let parseOk = false;
      let lastParseError = null;

      try {
        mermaid.parse(sanitizedCode);
        parseOk = true;
      } catch (parseError) {
        lastParseError = parseError.message;
        console.warn(
          "[Mermaid] Parse error na sanitização normal, tentando sanitização agressiva:",
          parseError.message
        );

        // --- Tentativa 2: Sanitização agressiva ---
        sanitizedCode = this.aggressiveSanitize(originalCode);
        try {
          mermaid.parse(sanitizedCode);
          parseOk = true;
          console.log("[Mermaid] Sanitização agressiva resolveu o problema");
        } catch (parseError2) {
          lastParseError = parseError2.message;
          console.warn(
            "[Mermaid] Parse error mesmo após sanitização agressiva:",
            parseError2.message
          );
        }
      }

      // Se o parse falhou, mostra fallback SEM substituir o DOM primeiro
      if (!parseOk) {
        console.warn(
          "[Mermaid] Pulando renderização (parse falhou), exibindo fallback"
        );
        this._createFallback(pre, originalCode, lastParseError, container);
        failCount++;
        continue;
      }

      // --- Parse OK: agora substitui o DOM ---
      const div = document.createElement("div");
      div.className = "mermaid";
      div.textContent = sanitizedCode;

      const parent = pre.parentNode;
      if (!parent) {
        failCount++;
        continue;
      }
      parent.replaceChild(div, pre);

      // --- Tenta renderizar com mermaid.run() ---
      try {
        await mermaid.run({ nodes: [div] });
        successCount++;
        console.log("[Mermaid] Diagrama renderizado com sucesso");
      } catch (e) {
        console.warn("[Mermaid] Render error:", e);
        this._createFallback(div, originalCode, e.message, container);
        failCount++;
      }
    }

    console.log(
      `[Mermaid] Renderização concluída: ${successCount} sucessos, ${failCount} falhas`
    );
  }

  _escapeHtml(text) {
    const div = document.createElement("div");
    div.appendChild(document.createTextNode(text));
    return div.innerHTML;
  }
}

// =============================================================================
// ExamManager — Lógica de Navegação, Respostas, Resultado da Prova
// =============================================================================

class ExamManager {
  constructor(state, ui, api, mermaidRenderer) {
    this.state = state;
    this.ui = ui;
    this.api = api;
    this.mermaidRenderer = mermaidRenderer;
    this.questionsModal = null;
    this.resultModal = null;
  }

  /** Inicializa os modais Bootstrap. */
  init() {
    this.questionsModal = new bootstrap.Modal(
      document.getElementById("questionsModal"),
      { backdrop: "static", keyboard: false }
    );
    this.resultModal = new bootstrap.Modal(
      document.getElementById("resultModal"),
      { backdrop: "static", keyboard: false }
    );
  }

  /** Abre o modal de prova, gerando questões se necessário. */
  async openExam() {
    if (!this.state.generatedMarkdown) {
      alert("Processe um conteúdo primeiro.");
      return;
    }

    const body = document.getElementById("questionsBody");
    const prevBtn = document.getElementById("prevQuestionBtn");
    const nextBtn = document.getElementById("nextQuestionBtn");
    const finishBtn = document.getElementById("finishExamBtn");
    const exportZipBtn = document.getElementById("exportZipBtn");

    // Show loading state
    body.innerHTML = `
      <div class="text-center py-4">
        <div class="spinner-border text-primary" role="status">
          <span class="visually-hidden">Carregando...</span>
        </div>
        <p class="mt-2 text-muted">Gerando questões... Aguarde.</p>
      </div>
    `;
    prevBtn.classList.add("d-none");
    nextBtn.classList.add("d-none");
    finishBtn.classList.add("d-none");
    exportZipBtn.classList.add("d-none");
    this.questionsModal.show();

    // If questions were pre-generated, use them
    if (
      this.state.questionsData &&
      this.state.questionsData.questions &&
      this.state.questionsData.questions.length > 0
    ) {
      this._renderQuestions(this.state.questionsData);
      return;
    }

    // Otherwise, fetch from backend
    try {
      const title =
        ImportExportManager.getTitleFromMarkdown(this.state.generatedMarkdown) ||
        "Conteúdo";
      const data = await this.api.generateQuestions(
        this.state.generatedMarkdown,
        title
      );
      this.state.questionsData = data;
      this._renderQuestions(data);
    } catch (e) {
      body.innerHTML = `<div class="alert alert-danger">Erro ao gerar questões: ${e.message}</div>`;
    }
  }

  /** Renderiza as questões no modal (uma por vez com navegação). */
  _renderQuestions(data) {
    const prevBtn = document.getElementById("prevQuestionBtn");
    const nextBtn = document.getElementById("nextQuestionBtn");
    const finishBtn = document.getElementById("finishExamBtn");
    const exportZipBtn = document.getElementById("exportZipBtn");

    this.state.userAnswers = {};
    this.state.currentQuestionIndex = 0;

    // Show navigation buttons
    prevBtn.classList.remove("d-none");
    nextBtn.classList.remove("d-none");
    finishBtn.classList.remove("d-none");
    exportZipBtn.classList.remove("d-none");

    this._renderSingleQuestion(data);
  }

  /** Renderiza a questão atual. */
  _renderSingleQuestion(data) {
    const body = document.getElementById("questionsBody");
    const prevBtn = document.getElementById("prevQuestionBtn");
    const nextBtn = document.getElementById("nextQuestionBtn");
    const finishBtn = document.getElementById("finishExamBtn");

    if (!data.questions || data.questions.length === 0) {
      body.innerHTML = `<div class="alert alert-warning">Nenhuma questão foi gerada. Tente novamente.</div>`;
      prevBtn.classList.add("d-none");
      nextBtn.classList.add("d-none");
      finishBtn.classList.add("d-none");
      return;
    }

    const total = data.questions.length;
    const q = data.questions[this.state.currentQuestionIndex];
    const idx = this.state.currentQuestionIndex;

    // Progress bar
    let html = `<div class="question-progress-bar mb-3">
      <div class="d-flex justify-content-between align-items-center mb-1">
        <small class="text-muted">Questão ${idx + 1} de ${total}</small>
        <small class="text-muted">${Math.round(((idx + 1) / total) * 100)}%</small>
      </div>
      <div class="progress" style="height: 6px;">
        <div class="progress-bar" role="progressbar" style="width: ${((idx + 1) / total) * 100}%"></div>
      </div>
    </div>`;

    // Question card
    html += `<div class="question-card mb-3" id="q-${q.id}">
      <div class="question-header">
        <span class="question-number">Questão ${idx + 1}</span>
      </div>
      <div class="question-enunciado">${marked.parse(q.enunciado)}</div>`;

    // Diagram if present
    if (q.diagrama) {
      html += `<div class="question-diagram mb-2">
        <pre><code class="language-mermaid">${q.diagrama}</code></pre>
      </div>`;
    }

    html += `<div class="question-alternatives">`;
    const letters = ["A", "B", "C", "D", "E"];
    letters.forEach((letter) => {
      const altText = q.alternativas[letter] || "";
      const checked =
        this.state.userAnswers[q.id] === letter ? "checked" : "";
      html += `<div class="alternative-item ${checked ? "selected" : ""}" data-question-id="${q.id}" data-letter="${letter}">
        <div class="form-check">
          <input
            class="form-check-input"
            type="radio"
            name="q-${q.id}"
            id="q-${q.id}-${letter}"
            value="${letter}"
            ${checked}
          />
          <label class="form-check-label" for="q-${q.id}-${letter}">
            <strong>${letter})</strong> ${altText}
          </label>
        </div>
      </div>`;
    });

    html += `</div>`; // question-alternatives
    html += `</div>`; // question-card

    body.innerHTML = html;

    // Show/hide navigation buttons
    prevBtn.classList.toggle("d-none", idx === 0);
    nextBtn.classList.toggle("d-none", idx >= total - 1);
    finishBtn.classList.toggle("d-none", idx < total - 1);

    // Render Mermaid diagrams inside the modal
    setTimeout(() => this.mermaidRenderer.renderDiagrams(body), 300);

    // Track user answers
    body.querySelectorAll('input[type="radio"]').forEach((input) => {
      input.addEventListener("change", () => {
        const qId = parseInt(
          input.closest(".alternative-item").dataset.questionId
        );
        this.state.userAnswers[qId] = input.value;
      });
    });

    // Click on alternative item (not just radio)
    body.querySelectorAll(".alternative-item").forEach((item) => {
      item.addEventListener("click", (e) => {
        if (e.target.tagName === "INPUT") return;
        const radio = item.querySelector('input[type="radio"]');
        if (radio) {
          radio.checked = true;
          radio.dispatchEvent(new Event("change"));
          // Update visual selection
          item
            .closest(".question-alternatives")
            .querySelectorAll(".alternative-item")
            .forEach((ai) => ai.classList.remove("selected"));
          item.classList.add("selected");
        }
      });
    });
  }

  /** Navega para a questão anterior. */
  prevQuestion() {
    if (this.state.currentQuestionIndex > 0 && this.state.questionsData) {
      this.state.currentQuestionIndex--;
      this._renderSingleQuestion(this.state.questionsData);
    }
  }

  /** Navega para a próxima questão. */
  nextQuestion() {
    if (
      this.state.questionsData &&
      this.state.currentQuestionIndex <
        this.state.questionsData.questions.length - 1
    ) {
      this.state.currentQuestionIndex++;
      this._renderSingleQuestion(this.state.questionsData);
    }
  }

  /** Finaliza a prova e mostra o resultado. */
  finishExam() {
    if (!this.state.questionsData || !this.state.questionsData.questions) return;

    // Close questions modal
    const questionsModalEl = document.getElementById("questionsModal");
    const questionsModal = bootstrap.Modal.getInstance(questionsModalEl);
    if (questionsModal) questionsModal.hide();

    // Render and open result modal
    this._renderResult(this.state.questionsData);
    this.resultModal.show();
  }

  /** Renderiza o resultado da prova. */
  _renderResult(data) {
    const body = document.getElementById("resultBody");
    if (!data.questions || data.questions.length === 0) {
      body.innerHTML = `<div class="alert alert-warning">Nenhum dado disponível.</div>`;
      return;
    }

    let correctCount = 0;
    const total = data.questions.length;

    data.questions.forEach((q) => {
      if (this.state.userAnswers[q.id] === q.correta) correctCount++;
    });

    const percentage = Math.round((correctCount / total) * 100);
    let gradeClass = "text-danger";
    let gradeEmoji = "😢";
    if (percentage >= 90) {
      gradeClass = "text-success";
      gradeEmoji = "🏆";
    } else if (percentage >= 70) {
      gradeClass = "text-primary";
      gradeEmoji = "👏";
    } else if (percentage >= 50) {
      gradeClass = "text-warning";
      gradeEmoji = "📚";
    }

    let html = `<div class="result-score text-center mb-4 p-4 bg-light rounded">
      <div class="result-emoji" style="font-size: 3rem;">${gradeEmoji}</div>
      <h2 class="${gradeClass} fw-bold mt-2">${correctCount}/${total} corretas</h2>
      <div class="progress mb-2" style="height: 20px; max-width: 300px; margin: 0 auto;">
        <div class="progress-bar ${percentage >= 70 ? "bg-success" : percentage >= 50 ? "bg-warning" : "bg-danger"}"
             role="progressbar" style="width: ${percentage}%">
          ${percentage}%
        </div>
      </div>
      <p class="text-muted mb-0">
        ${percentage >= 90 ? "Excelente! Domínio completo do conteúdo." : percentage >= 70 ? "Muito bom! Precisa revisar alguns pontos." : percentage >= 50 ? "Bom, mas precisa estudar mais." : "Estude novamente o conteúdo e tente outra vez."}
      </p>
    </div>`;

    data.questions.forEach((q, idx) => {
      const userAnswer = this.state.userAnswers[q.id];
      const isCorrect = userAnswer === q.correta;
      const qClass = isCorrect ? "correct" : "incorrect";
      const icon = isCorrect ? "✅" : "❌";

      html += `<div class="result-question ${qClass} mb-3 p-3 border rounded">
        <div class="d-flex justify-content-between align-items-start mb-2">
          <span class="fw-bold">${icon} Questão ${idx + 1}</span>
          <span class="badge ${isCorrect ? "bg-success" : "bg-danger"}">${isCorrect ? "Correta" : "Incorreta"}</span>
        </div>
        <div class="result-enunciado mb-2">${marked.parse(q.enunciado)}</div>`;

      if (q.diagrama) {
        html += `<div class="question-diagram mb-2">
          <pre><code class="language-mermaid">${q.diagrama}</code></pre>
        </div>`;
      }

      html += `<div class="result-alternatives">`;
      const letters = ["A", "B", "C", "D", "E"];
      letters.forEach((letter) => {
        const altText = q.alternativas[letter] || "";
        let altClass = "";
        if (letter === q.correta) altClass = "correct";
        else if (letter === userAnswer && userAnswer !== q.correta)
          altClass = "incorrect";
        const mark =
          letter === q.correta
            ? " ✓"
            : letter === userAnswer && userAnswer !== q.correta
            ? " ✗"
            : "";
        html += `<div class="alternative-item ${altClass} mb-1">
          <strong>${letter})</strong> ${altText}${mark ? `<span class="float-end fw-bold">${mark}</span>` : ""}
        </div>`;
      });
      html += `</div>`;

      html += `<div class="result-explicacao mt-2 p-2 bg-light rounded">
        <small><strong>💡 Explicação:</strong> ${q.explicacao}</small>
      </div>`;

      html += `</div>`;
    });

    body.innerHTML = html;
    setTimeout(() => this.mermaidRenderer.renderDiagrams(body), 300);
  }
}

// =============================================================================
// ImportExportManager — Lógica de Import/Export
// =============================================================================

class ImportExportManager {
  constructor(state, ui, api, mermaidRenderer) {
    this.state = state;
    this.ui = ui;
    this.api = api;
    this.mermaidRenderer = mermaidRenderer;
  }

  /**
   * Extrai o título do markdown para usar como nome de arquivo.
   * @param {string} markdown
   * @returns {string|null}
   */
  static getTitleFromMarkdown(markdown) {
    if (!markdown) return null;
    const match = markdown.match(/^#\s+(.*?)(?:\n|$)/m);
    if (!match) return null;
    let title = match[1].trim();
    title = title.replace(/^[^\w\s]{1,3}\s*/, "");
    title = title.replace(/^(Aula|Resumo|Documento)[:\s]*/i, "");
    title = title.trim();
    if (!title) return null;
    title = title
      .replace(/[<>:"/\\|?*]/g, "")
      .replace(/\s+/g, "-")
      .replace(/-+/g, "-")
      .replace(/^-+|-+$/g, "")
      .toLowerCase()
      .slice(0, 80);
    return title || null;
  }

  /** Dispara o download do markdown como arquivo .md. */
  downloadMarkdown() {
    const title = ImportExportManager.getTitleFromMarkdown(
      this.state.generatedMarkdown
    );
    const baseName = title || "conteudo-gerado";
    const filename = baseName + ".md";
    const blob = new Blob([this.state.generatedMarkdown], {
      type: "text/markdown",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  /** Abre o seletor de arquivo para importação. */
  triggerImport() {
    const input = document.getElementById("importFileInput");
    if (input) input.click();
  }

  /** Processa o arquivo selecionado para importação. */
  async handleImportFile(file) {
    if (!file) return;

    const ext = file.name.split(".").pop().toLowerCase();
    if (ext !== "md" && ext !== "zip") {
      alert("Formato não suportado. Aceitos apenas .md e .zip.");
      return;
    }

    this.ui.showLoading("Importando...", "Processando arquivo...");
    await forcePaint();

    try {
      const data = await this.api.importFile(file);

      if (data.questions) {
        // ZIP com questões: restaura markdown + questionsData
        this.state.setMarkdownAndQuestions(data.markdown, data.questions);
      } else {
        // Apenas .md: restaura só markdown, limpa questionsData
        this.state.setMarkdown(data.markdown);
      }

      // Renderiza o markdown
      this.ui.renderMarkdown(data.markdown);
      await this.mermaidRenderer.renderDiagrams(this.ui.output);

      console.log("[Import] Importação concluída com sucesso");
    } catch (e) {
      this.ui.showError(`Erro ao importar: ${e.message}`);
    } finally {
      this.ui.hideLoading();
    }
  }

  /** Exporta ZIP com markdown e questões (se houver). */
  async exportZip() {
    if (!this.state.generatedMarkdown) {
      alert("Nenhum conteúdo para exportar.");
      return;
    }

    const title =
      ImportExportManager.getTitleFromMarkdown(this.state.generatedMarkdown) ||
      "conteudo";

    this.ui.showLoading("Exportando ZIP...", "Gerando arquivo...");
    await forcePaint();

    try {
      const blob = await this.api.exportZip(
        this.state.generatedMarkdown,
        this.state.questionsData,
        title
      );

      // Dispara o download
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${title}.zip`;
      a.click();
      URL.revokeObjectURL(url);

      console.log("[Export] ZIP exportado com sucesso");
    } catch (e) {
      alert(`Erro ao exportar ZIP: ${e.message}`);
    } finally {
      this.ui.hideLoading();
    }
  }
}

// =============================================================================
// Inicialização
// =============================================================================

document.addEventListener("DOMContentLoaded", async () => {
  // ── Instancia módulos ──────────────────────────────────────────────
  const state = new AppState();
  const ui = new UIManager();
  const api = new ApiClient();
  const mermaidRenderer = new MermaidRenderer();
  const examManager = new ExamManager(state, ui, api, mermaidRenderer);
  const importExportManager = new ImportExportManager(
    state,
    ui,
    api,
    mermaidRenderer
  );

  // ── Mermaid initialization ─────────────────────────────────────────
  if (typeof mermaid !== "undefined") {
    mermaid.initialize({
      startOnLoad: false,
      theme: "default",
      securityLevel: "loose",
      maxTextSize: 100000,
    });
  }

  // ── Inicializa modais ──────────────────────────────────────────────
  examManager.init();

  // ── Fetch backend config on load ───────────────────────────────────
  try {
    const res = await fetch("/api/config");
    const config = await res.json();
    const languageSelector = document.getElementById("languageSelector");
    if (config.transcription_provider === "deepgram") {
      languageSelector.classList.remove("d-none");
    }
  } catch (e) {
    console.warn("Não foi possível carregar config do backend:", e);
  }

  // ── Input mode selection ───────────────────────────────────────────
  const modeRadios = document.querySelectorAll('input[name="inputMode"]');
  const modeCards = {
    file: document.getElementById("modeFileCard"),
    youtube: document.getElementById("modeYoutubeCard"),
  };
  const modeContents = {
    file: document.getElementById("fileContent"),
    youtube: document.getElementById("youtubeUrlContent"),
  };
  const modeInputs = {
    file: document.getElementById("uploadFile"),
    youtube: document.getElementById("youtubeUrl"),
  };

  function selectMode(mode) {
    Object.values(modeCards).forEach((card) =>
      card.classList.remove("selected")
    );
    Object.values(modeContents).forEach((el) => (el.style.display = "none"));

    if (mode && modeCards[mode]) {
      modeCards[mode].classList.add("selected");
      modeContents[mode].style.display = "block";
    }
  }

  modeRadios.forEach((radio) => {
    radio.addEventListener("change", () => {
      if (radio.checked) {
        selectMode(radio.value);
        for (const [key, input] of Object.entries(modeInputs)) {
          if (key !== radio.value) {
            if (input.type === "file") input.value = "";
            else input.value = "";
          }
        }
      }
    });
  });

  document.querySelectorAll(".input-mode-option").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      const radio = card.querySelector('input[type="radio"]');
      if (radio) {
        radio.checked = true;
        radio.dispatchEvent(new Event("change"));
      }
    });
  });

  // ── Agent selection (Resumidor / Educador) ───────────────────────────
  const agentRadios = document.querySelectorAll('input[name="aiAgent"]');
  const agentCards = {
    resumidor: document.getElementById("agentResumidorCard"),
    educador: document.getElementById("agentEducadorCard"),
  };
  const generateQuestionsWrapper = document.getElementById("generateQuestionsWrapper");
  const generateQuestionsCheckbox = document.getElementById("generateQuestions");

  function selectAgent(agent) {
    Object.values(agentCards).forEach((card) =>
      card.classList.remove("selected")
    );
    if (agentCards[agent]) {
      agentCards[agent].classList.add("selected");
    }
    // Show/hide "Gerar Questões" checkbox based on agent
    if (agent === "educador") {
      generateQuestionsWrapper.classList.remove("d-none");
    } else {
      generateQuestionsWrapper.classList.add("d-none");
      generateQuestionsCheckbox.checked = false;
    }
  }

  agentRadios.forEach((radio) => {
    radio.addEventListener("change", () => {
      if (radio.checked) {
        selectAgent(radio.value);
      }
    });
  });

  document.querySelectorAll(".agent-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.tagName === "INPUT") return;
      const radio = card.querySelector('input[type="radio"]');
      if (radio) {
        radio.checked = true;
        radio.dispatchEvent(new Event("change"));
      }
    });
  });

  // ── Process button ──────────────────────────────────────────────────
  document.getElementById("processBtn").addEventListener("click", async () => {
    const btn = document.getElementById("processBtn");

    // ── VALIDAÇÃO ANTES de mostrar o loading ──────────────────────
    const selectedMode = document.querySelector(
      'input[name="inputMode"]:checked'
    )?.value;

    if (selectedMode === "youtube") {
      const youtubeUrl = document.getElementById("youtubeUrl").value.trim();
      if (!youtubeUrl) {
        alert("Por favor, insira uma URL do YouTube.");
        return;
      }
      const youtubeRegex = /^(https?:\/\/)?(www\.)?(youtube\.com\/(watch\?v=|embed\/|v\/|shorts\/|live\/)|youtu\.be\/)[a-zA-Z0-9_-]{11}/;
      if (!youtubeRegex.test(youtubeUrl)) {
        alert("Apenas links do YouTube (youtube.com ou youtu.be) são aceitos.\n\nPara outros vídeos, use a opção 'Upload de Arquivo'.");
        return;
      }
    } else if (selectedMode === "file") {
      const uploadFile = document.getElementById("uploadFile").files[0];
      if (!uploadFile) {
        alert("Por favor, selecione um arquivo.");
        return;
      }
    } else {
      alert("Por favor, selecione um modo de entrada.");
      return;
    }

    // ── VALIDAÇÃO PASSOU → desabilita botão e mostra loading ──────
    btn.disabled = true;
    ui.showLoading("Processando...", "Aguarde, pode levar vários minutos.");

    // Força o navegador a pintar o overlay (forced reflow + double RAF)
    await forcePaint();

    const agent = document.querySelector('input[name="aiAgent"]:checked')?.value || "resumidor";
    const model = document.getElementById("deepseekModel").value;

    const formData = new FormData();
    formData.append("agent", agent);
    formData.append("prompt", document.getElementById("prompt").value);
    formData.append("model", model);
    formData.append("language", document.getElementById("languageSelect").value);

    if (selectedMode === "youtube") {
      formData.append("youtube_url", document.getElementById("youtubeUrl").value.trim());
    } else {
      formData.append("file", document.getElementById("uploadFile").files[0]);
    }

    try {
      const data = await api.processContent(formData);
      state.setMarkdown(data.markdown);

      // Render markdown
      ui.renderMarkdown(data.markdown);
      await mermaidRenderer.renderDiagrams(ui.output);
    } catch (e) {
      ui.showError(e.message);
    } finally {
      btn.disabled = false;
      ui.hideLoading();
    }
  });

  // ── Dropdown: Baixar Markdown ───────────────────────────────────────
  document.getElementById("downloadOption").addEventListener("click", (e) => {
    e.preventDefault();
    if (state.generatedMarkdown) {
      importExportManager.downloadMarkdown();
    }
  });

  // ── Botão Importar (sempre visível) ──────────────────────────────────
  document.getElementById("importBtn").addEventListener("click", () => {
    importExportManager.triggerImport();
  });

  document.getElementById("importFileInput").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) {
      importExportManager.handleImportFile(file);
    }
    // Reset input so the same file can be re-imported
    e.target.value = "";
  });

  // ── Dropdown: Gerar Prova ───────────────────────────────────────────
  document.getElementById("questionsOption").addEventListener("click", async (e) => {
    e.preventDefault();
    await examManager.openExam();
  });

  // ── Navigation: Previous Question ───────────────────────────────────
  document.getElementById("prevQuestionBtn").addEventListener("click", () => {
    examManager.prevQuestion();
  });

  // ── Navigation: Next Question ───────────────────────────────────────
  document.getElementById("nextQuestionBtn").addEventListener("click", () => {
    examManager.nextQuestion();
  });

  // ── Finish Exam ─────────────────────────────────────────────────────
  document.getElementById("finishExamBtn").addEventListener("click", () => {
    examManager.finishExam();
  });

  // ── Export ZIP (no modal da prova) ──────────────────────────────────
  document.getElementById("exportZipBtn").addEventListener("click", () => {
    importExportManager.exportZip();
  });

  // ── Reset modal state when closed ───────────────────────────────────
  document.getElementById("questionsModal").addEventListener("hidden.bs.modal", () => {
    const prevBtn = document.getElementById("prevQuestionBtn");
    const nextBtn = document.getElementById("nextQuestionBtn");
    const finishBtn = document.getElementById("finishExamBtn");
    const exportZipBtn = document.getElementById("exportZipBtn");
    prevBtn.classList.add("d-none");
    nextBtn.classList.add("d-none");
    finishBtn.classList.add("d-none");
    exportZipBtn.classList.add("d-none");
  });
});
