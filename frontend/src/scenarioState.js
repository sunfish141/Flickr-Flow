export const HISTORY_LIMIT = 128;

export const emptyScenario = () => ({ history: [], cursor: 0, ignitions: [], source: 'placed' });

// Frames retain the complete model state, including fuel and burned-cell masks.
// The timeline's step numbers may advance by two for historical comparisons.
export function scenarioReducer(state, action) {
  switch (action.type) {
    case 'reset':
      return emptyScenario();
    case 'replace':
      return { history: [action.frame], cursor: 0, ignitions: action.ignitions ?? [], source: action.source };
    case 'append': {
      const history = [...state.history, action.frame].slice(-HISTORY_LIMIT);
      return { ...state, history, cursor: history.length - 1 };
    }
    case 'replay':
      // Ignore repeated clicks from a render that has already advanced.
      return state.cursor === action.cursor && state.cursor < state.history.length - 1
        ? { ...state, cursor: state.cursor + 1 } : state;
    case 'seek': {
      const cursor = state.history.findIndex(frame => frame.state.step_index === action.step);
      return cursor < 0 ? state : { ...state, cursor };
    }
    default:
      return state;
  }
}
