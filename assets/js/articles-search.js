(() => {
  'use strict';

  /*
   * Optional helper for the unified search page.
   * Load this BEFORE search.js if you want article-level results.
   */

  async function loadArticlesIndex() {
    const response = await fetch('../assets/data/articles-index.json', { cache: 'no-store' });
    if (!response.ok) {
      throw new Error(`articles-index.json HTTP ${response.status}`);
    }
    const data = await response.json();
    return Array.isArray(data.articles) ? data.articles : [];
  }

  function normalizeDigits(value) {
    return String(value || '')
      .replace(/[۰-۹]/g, d => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(d)))
      .replace(/[٠-٩]/g, d => String('٠١٢٣٤٥٦٧٨٩'.indexOf(d)));
  }

  function normalizeArticleId(value) {
    return normalizeDigits(value)
      .replace(/[–—−]/g, '-')
      .replace(/\s*-\s*/g, '-')
      .trim();
  }

  function extractArticleIdFromQuery(query) {
    const normalized = normalizeDigits(query).replace(/[–—−]/g, '-');
    const match = normalized.match(/(?:ماده\s*)?([0-9]+(?:\s*-\s*[0-9]+)?)/i);
    return match ? normalizeArticleId(match[1]) : null;
  }

  window.NazmDadArticleSearch = {
    loadArticlesIndex,
    normalizeArticleId,
    extractArticleIdFromQuery
  };
})();
