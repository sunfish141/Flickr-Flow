// A canceled fetch may still resolve (or finish server-side). Ticket identity
// prevents its callbacks and cleanup from affecting a replacement request.
export class RequestCoordinator {
  active = null;

  cancel(notify = true) {
    const ticket = this.active;
    this.active = null;
    if (!ticket) return false;
    ticket.controller.abort();
    if (notify) ticket.onBusy(null);
    return true;
  }

  async run(kind, operation, { onSuccess, onError, onBusy }) {
    if (this.active) return false;
    const ticket = { kind, controller: new AbortController(), onBusy };
    this.active = ticket;
    onBusy(kind);
    try {
      const result = await operation(ticket.controller.signal);
      if (this.active !== ticket) return false;
      onSuccess(result);
      return true;
    } catch (error) {
      if (this.active === ticket && error.name !== 'AbortError') onError(error);
      return false;
    } finally {
      if (this.active === ticket) {
        this.active = null;
        onBusy(null);
      }
    }
  }
}
