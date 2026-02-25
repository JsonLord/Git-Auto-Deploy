import React, { Component } from 'react';
import axios from 'axios';
import './HuggingFaceStatus.scss';

class HuggingFaceStatus extends Component {
  constructor(props) {
    super(props);
    this.state = {
      info: null,
      loading: false,
      error: null,
      showDocs: false
    };
  }

  checkConnection = () => {
    this.setState({ loading: true, error: null });
    const host = window.location.protocol + '//' + window.location.host;
    axios.get(host + '/api/hf/check')
      .then(res => {
        this.setState({ info: res.data, loading: false });
      })
      .catch(err => {
        this.setState({ error: 'Failed to fetch HF status', loading: false });
      });
  }

  toggleDocs = () => {
    this.setState({ showDocs: !this.state.showDocs });
  }

  render() {
    const { info, loading, error, showDocs } = this.state;

    return (
      <div className="HuggingFaceStatus">
        <div className="actions">
          <button onClick={this.checkConnection} disabled={loading}>
            {loading ? 'Checking...' : 'Check HF Connection'}
          </button>
          <button onClick={this.toggleDocs}>
            {showDocs ? 'Hide Documentation' : 'Show Documentation'}
          </button>
        </div>

        {showDocs && (
          <div className="docs">
            <h3>Documentation: Git-HF-Deployment</h3>
            <p>This application is modified to work exclusively with Hugging Face Spaces and integrate with the Jules agent for automated fixes.</p>
            <ul>
              <li><strong>Hugging Face Deploy:</strong> Pushes updates to HF Spaces and monitors build status.</li>
              <li><strong>Jules Integration:</strong> On deployment failure, a GitHub issue is created with the <code>jules:run</code> label and build logs. Jules will then fix the code and redeploy.</li>
              <li><strong>Web UI:</strong> Provides real-time status of webhook events and deployment logs.</li>
            </ul>
          </div>
        )}

        {error && <div className="error">{error}</div>}

        {info && (
          <div className="info">
            <p><strong>Connection Status:</strong> {info.hf_connection}</p>
            <p><strong>Space ID:</strong> {info.space_id}</p>
            <h4>Parameters / Env Vars:</h4>
            <pre>{JSON.stringify(info.env_vars, null, 2)}</pre>
            {info.space_metadata && (
                <>
                    <h4>Space Metadata:</h4>
                    <pre>{JSON.stringify(info.space_metadata, null, 2)}</pre>
                </>
            )}
          </div>
        )}
      </div>
    );
  }
}

export default HuggingFaceStatus;
