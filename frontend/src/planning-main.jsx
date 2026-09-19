import React, { Component } from 'react';
import { createRoot } from 'react-dom/client';
import OfflineApp from './OfflineApp.jsx';
import './planning.css';

class Boundary extends Component {
  state = { error: null };
  static getDerivedStateFromError(error) { return { error }; }
  render() {
    return this.state.error ? <main><h1>Planner interface stopped</h1><p>Reload to recover the last acknowledged save. The SQLite scenario store is retained.</p><pre>{String(this.state.error)}</pre></main> : this.props.children;
  }
}
createRoot(document.getElementById('root')).render(<Boundary><OfflineApp /></Boundary>);
