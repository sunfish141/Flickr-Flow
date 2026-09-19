export const selectionKey = (point, local = false) =>
  `${local && point.status !== 'historical' ? 'local' : point.status}:${point.cell_id}`;

export const polygonCellVisible = (point, visibility) =>
  (visibility.active && point.active_area_m2 > 0) ||
  (visibility.burned && point.burned_area_m2 > 0);
