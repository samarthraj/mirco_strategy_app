/**
 * Parses V2 API grid responses into flat table data.
 * Handles multi-form attributes, metric columns, and formatted values.
 */

export interface GridAttribute {
  id: string;
  name: string;
  forms: { id: string; name: string; dataType?: string }[];
}

export interface GridMetric {
  id: string;
  name: string;
  dataType?: string;
}

export interface ParsedColumn {
  name: string;
  id: string;
  type: 'attribute' | 'metric';
  formName?: string;
}

export interface ParsedGrid {
  columns: ParsedColumn[];
  rows: string[][];
  totalRows: number;
  pageOffset: number;
  pageLimit: number;
}

export function parseV2Definition(data: any): { attributes: GridAttribute[]; metrics: GridMetric[] } {
  const grid = data?.definition?.grid;
  if (!grid) {
    const available = data?.definition?.availableObjects;
    if (available) {
      return {
        attributes: (available.attributes || []).map((a: any) => ({
          id: a.id,
          name: a.name,
          forms: a.forms?.map((f: any) => ({ id: f.id, name: f.name, dataType: f.dataType })) || [],
        })),
        metrics: (available.metrics || []).map((m: any) => ({
          id: m.id,
          name: m.name,
          dataType: m.dataType,
        })),
      };
    }
    return { attributes: [], metrics: [] };
  }

  const attributes: GridAttribute[] = (grid.rows || []).map((r: any) => ({
    id: r.id,
    name: r.name,
    forms: r.forms?.map((f: any) => ({ id: f.id, name: f.name, dataType: f.dataType })) || [],
  }));

  const metrics: GridMetric[] = [];
  for (const col of grid.columns || []) {
    if (col.elements) {
      for (const el of col.elements) {
        metrics.push({ id: el.id, name: el.name, dataType: el.dataType });
      }
    } else {
      metrics.push({ id: col.id, name: col.name, dataType: col.dataType });
    }
  }

  return { attributes, metrics };
}

export function parseV2Grid(data: any): ParsedGrid {
  const grid = data?.definition?.grid;
  const rawData = data?.data;
  const paging = rawData?.paging || {};

  if (!grid || !rawData) {
    return { columns: [], rows: [], totalRows: 0, pageOffset: 0, pageLimit: 0 };
  }

  const rowAttrs: any[] = grid.rows || [];
  const colMetrics: any[] = grid.columns || [];

  // Build columns: one per attribute form, one per metric
  const columns: ParsedColumn[] = [];

  for (const attr of rowAttrs) {
    const forms = attr.forms || [];
    if (forms.length <= 1) {
      columns.push({ name: attr.name, id: attr.id, type: 'attribute' });
    } else {
      for (const form of forms) {
        columns.push({
          name: `${attr.name} (${form.name})`,
          id: attr.id,
          type: 'attribute',
          formName: form.name,
        });
      }
    }
  }

  for (const col of colMetrics) {
    if (col.elements) {
      for (const el of col.elements) {
        columns.push({ name: el.name, id: el.id, type: 'metric' });
      }
    } else {
      columns.push({ name: col.name, id: col.id, type: 'metric' });
    }
  }

  // Parse row data
  const rows: string[][] = [];
  const rowHeaders: number[][] = rawData.headers?.rows || [];
  const attrElements = rowAttrs.map((r: any) => r.elements || []);

  for (let i = 0; i < rowHeaders.length; i++) {
    const row: string[] = [];

    // Attribute values — handle multi-form
    for (let a = 0; a < rowAttrs.length; a++) {
      const elemIdx = rowHeaders[i][a];
      const elem = attrElements[a]?.[elemIdx];
      const forms = rowAttrs[a].forms || [];

      if (forms.length <= 1) {
        row.push(elem?.formValues?.[0] || elem?.name || String(elemIdx));
      } else {
        for (let f = 0; f < forms.length; f++) {
          row.push(elem?.formValues?.[f] || elem?.name || String(elemIdx));
        }
      }
    }

    // Metric values — prefer formatted
    const formatted = rawData.metricValues?.formatted?.[i] || [];
    const raw = rawData.metricValues?.raw?.[i] || [];
    const metricCount = Math.max(formatted.length, raw.length);
    for (let m = 0; m < metricCount; m++) {
      const val = formatted[m] ?? raw[m];
      row.push(val != null ? String(val) : '');
    }

    rows.push(row);
  }

  return {
    columns,
    rows,
    totalRows: paging.total ?? rows.length,
    pageOffset: paging.current ?? 0,
    pageLimit: paging.limit ?? rows.length,
  };
}
