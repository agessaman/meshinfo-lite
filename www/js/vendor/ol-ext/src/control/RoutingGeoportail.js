﻿/*	Copyright (c) 2018 Jean-Marc VIGLINO,
  released under the CeCILL-B license (French BSD license)
  (http://www.cecill.info/licences/Licence_CeCILL-B_V1-en.txt).
*/
import ol_control_Control from 'ol/control/Control.js'
import ol_Feature from 'ol/Feature.js'
import ol_ext_element from '../util/element.js';
import ol_control_SearchGeoportail from './SearchGeoportail.js'
import ol_source_Vector from 'ol/source/Vector.js'
import ol_geom_Point from 'ol/geom/Point.js'
import {transform as ol_proj_transform} from 'ol/proj.js'
import { ol_coordinate_equal } from '../geom/GeomUtils.js'
import ol_format_GeoJSON from 'ol/format/GeoJSON.js'

/** Geoportail routing Control.
 * @constructor
 * @extends {ol_control_Control}
 * @fires select
 * @fires change:input
 * @fires routing:start
 * @fires routing
 * @fires step:select
 * @fires step:hover
 * @fires error
 * @fires abort
 * @param {Object=} options
 *	@param {string} options.className control class name
 *	@param {string} [options.leng=en] control language
 *	@param {string | undefined} [options.apiKey] the service api key.
 *	@param {string | undefined} options.authentication: basic authentication for the service API as btoa("login:pwd")
 *	@param {Element | string | undefined} options.target Specify a target if you want the control to be rendered outside of the map's viewport.
 *	@param {string | undefined} options.label Text label to use for the search button, default "search"
 *	@param {string | undefined} options.placeholder placeholder, default "Search..."
 *	@param {string | undefined} options.inputLabel label for the input, default none
 *	@param {string | undefined} options.noCollapse prevent collapsing on input blur, default false
 *	@param {number | undefined} options.typing a delay on each typing to start searching (ms) use -1 to prevent autocompletion, default 300.
 *	@param {integer | undefined} options.minLength minimum length to start searching, default 1
 *	@param {integer | undefined} options.maxItems maximum number of items to display in the autocomplete list, default 10
 *	@param {integer | undefined} options.maxHistory maximum number of items to display in history. Set -1 if you don't want history, default maxItems
 *	@param {function} options.getTitle a function that takes a feature and return the name to display in the index.
 *	@param {function} options.autocomplete a function that take a search string and callback function to send an array
 *	@param {number} options.timeout default 20s
 */
var ol_control_RoutingGeoportail = class olcontrolRoutingGeoportail extends ol_control_Control {
  constructor(options) {
    options = options || {};
    if (options.typing == undefined) options.typing = 300;
    options.apiKey = options.apiKey || 'itineraire';
    if (!options.search) options.search = {};
    options.search.apiKey = options.search.apiKey || 'essentiels';
    
    var element = document.createElement("DIV");
    super({
      element: element,
      target: options.target
    });
    
    var self = this;
    // Class name for history
    this._classname = options.className || 'search';

    this._source = new ol_source_Vector();
    this.set('lang', options.lang || 'en')

    // Authentication
    this._auth = options.authentication;

    var classNames = (options.className || "") + " ol-routing";
    if (!options.target) {
      classNames += " ol-unselectable ol-control";
    }
    element.setAttribute('class', classNames);
    if (!options.target) {
      var bt = ol_ext_element.create('BUTTON', { parent: element });
      bt.addEventListener('click', function () {
        element.classList.toggle('ol-collapsed');
      });
    }


    this.set('url', 'https://data.geopf.fr/navigation/itineraire')

    var content = ol_ext_element.create('DIV', { className: 'content', parent: element });

    var listElt = ol_ext_element.create('DIV', { className: 'search-input', parent: content });

    this._search = [];
    this.addSearch(listElt, options);
    this.addSearch(listElt, options);

    // Method
    ol_ext_element.createSwitch({
      className: 'ol-method',
      html: 'fastest',
      after: 'shortest',
      parent: content
    }).addEventListener('change', function(e) {
      self.setMethod(e.target.checked ? 'shortest' : 'fastest')
    })
    // Constraints
    var constraintsBlock = ol_ext_element.create('DIV', { className: 'ol-constraints', parent: content })
    var constraints = options.constraints || { autoroute: 'péages', tunnel: 'tunnels', pont: 'ponts' }
    Object.keys(constraints).forEach(function(c) {
      ol_ext_element.createCheck({
        after: constraints[c],
        value: c,
        checked: true,
        parent: constraintsBlock
      }).addEventListener('change', function(e) {
        self.setConstraint(c, e.target.checked ? false : true)
      })
    })
    // Mode
    ol_ext_element.create('I', { 
      className: 'ol-car', 
      title: options.carlabel || 'by car', 
      parent: content 
    }).addEventListener("click", function () {
        self.setMode('car');
      });
    ol_ext_element.create('I', { 
      className: 'ol-pedestrian', 
      title: options.pedlabel || 'pedestrian', 
      parent: content 
    }).addEventListener("click", function () {
        self.setMode('pedestrian');
      });
    // OK/cancel
    ol_ext_element.create('I', { className: 'ol-ok', title: options.runlabel || 'search', html: 'OK', parent: content })
      .addEventListener("click", function () {
        self.calculate();
      });
    ol_ext_element.create('I', { className: 'ol-cancel', html: 'cancel', parent: content })
      .addEventListener("click", function () {
        this.resultElement.innerHTML = '';
      }.bind(this));

    this.resultElement = document.createElement("DIV");
    this.resultElement.setAttribute('class', 'ol-result');
    element.appendChild(this.resultElement);

    this.setMode(options.mode || 'car');
    this.set('timeout', options.timeout || 20000);
  }
  /** Set the mode (car/pedestrian)
   * @param {string} mode
   * @param {boolean} [silent=false] pervent calculating
   */
  setMode(mode, silent) {
    this.set('mode', mode);
    this.element.querySelector(".ol-car").classList.remove("selected");
    this.element.querySelector(".ol-pedestrian").classList.remove("selected");
    this.element.querySelector(".ol-" + mode).classList.add("selected");
    if (!silent) {
      this.calculate();
    }
  }
  /** Set method (shortest, fastest)
   * @param {string} method
   * @param {boolean} [silent=false] pervent calculating
   */
  setMethod(method, silent) {
    this.set('method', method);
    if (!silent) {
      this.calculate();
    }
  }
  /** Add / remove constraint
   * @param {string} type
   * @param {string} [value]
   */
  setConstraint(type, value) {
    var c = this.get('constraint') || [];
    c[type] = value;
    this.set('constraint', c)
  }
  /** Add a new button
   * @param {string} className
   * @param {string} title
   * @param {string} info
   */
  addButton(className, title, info) {
    var bt = document.createElement("I");
    bt.setAttribute("class", className);
    bt.setAttribute("type", "button");
    bt.setAttribute("title", title);
    bt.innerHTML = info || '';
    this.element.appendChild(bt);
    return bt;
  }
  /** Get point source
   * @return {ol.source.Vector }
   */
  getSource() {
    return this._source;
  }
  /** reset
   * @private
   */
  _resetArray(element) {
    this._search = [];
    var q = element.parentNode.querySelectorAll('.search-input > div');
    q.forEach(function (d) {
      if (d.olsearch) {
        if (d.olsearch.get('feature')) {
          d.olsearch.get('feature').set('step', this._search.length);
          if (this._search.length === 0)
            d.olsearch.get('feature').set('pos', 'start');
          else if (this._search.length === q.length - 1)
            d.olsearch.get('feature').set('pos', 'end');
          else
            d.olsearch.get('feature').set('pos', '');
        }
        this._search.push(d.olsearch);
      }
    }.bind(this));
  }
  /** Remove a new search input
   * @private
   */
  removeSearch(element, options, after) {
    element.removeChild(after);
    if (after.olsearch.get('feature'))
      this._source.removeFeature(after.olsearch.get('feature'));
    if (this.getMap())
      this.getMap().removeControl(after.olsearch);
    this._resetArray(element);
  }
  /** Add a new search input
   * @private
   */
  addSearch(element, options, after) {
    var self = this;
    var div = ol_ext_element.create('DIV');
    if (after)
      element.insertBefore(div, after.nextSibling);
    else
      element.appendChild(div);

    ol_ext_element.create('BUTTON', { title: options.startlabel || 'use shift to add / ctrl to remove', parent: div })
      .addEventListener('click', function (e) {
        if (e.ctrlKey) {
          if (this._search.length > 2)
            this.removeSearch(element, options, div);
        } else if (e.shiftKey) {
          this.addSearch(element, options, div);
        }
      }.bind(this));

    var search = div.olsearch = new ol_control_SearchGeoportail({
      className: 'IGNF ol-collapsed',
      apiKey: options.search.apiKey,
      authentication: options.search.authentication,
      target: div,
      reverse: true
    });
    search._changeCounter = 0;
    this._resetArray(element);
    search.on('select', function (e) {
      search.setInput(e.search.fulltext);
      var f = search.get('feature');
      if (!f) {
        f = new ol_Feature(new ol_geom_Point(e.coordinate));
        search.set('feature', f);
        this._source.addFeature(f);
        // Check geometry change
        search.checkgeom = true;
        f.getGeometry().on('change', function () {
          if (search.checkgeom)
            this.onGeometryChange(search, f);
        }.bind(this));
      } else {
        search.checkgeom = false;
        if (!e.silent)
          f.getGeometry().setCoordinates(e.coordinate);
        search.checkgeom = true;
      }
      f.set('name', search.getTitle(e.search));
      f.set('step', this._search.indexOf(search));
      if (f.get('step') === 0)
        f.set('pos', 'start');
      else if (f.get('step') === this._search.length - 1)
        f.set('pos', 'end');
      search.set('selection', e.search);
    }.bind(this));
    search.element.querySelector('input').addEventListener('change', function () {
      search.set('selection', null);
      self.resultElement.innerHTML = '';
    });
    if (this.getMap())
      this.getMap().addControl(search);
  }
  /** Geometry has changed
   * @private
   */
  onGeometryChange(search, f, delay) {
    // Set current geom 
    var lonlat = ol_proj_transform(f.getGeometry().getCoordinates(), this.getMap().getView().getProjection(), 'EPSG:4326');
    search._handleSelect({
      x: lonlat[0],
      y: lonlat[1],
      fulltext: lonlat[0].toFixed(6) + ',' + lonlat[1].toFixed(6)
    }, true, { silent: true });

    // Try to revers geocode
    if (delay) {
      search._changeCounter--;
      if (!search._changeCounter) {
        search.reverseGeocode(f.getGeometry().getCoordinates(), { silent: true });
        return;
      }
    } else {
      search._changeCounter++;
      setTimeout(function () {
        this.onGeometryChange(search, f, true);
      }.bind(this), 1000);
    }
  }
  /**
   * Set the map instance the control is associated with
   * and add its controls associated to this map.
   * @param {_ol_Map_} map The map instance.
   */
  setMap(map) {
    super.setMap(map);

    for (var i = 0; i < this._search.length; i++) {
      var c = this._search[i];
      c.setMap(map);
    }
  }
  /** Get request data
   * @private
   */
  requestData(steps) {
    var start = steps[0];
    var end = steps[steps.length - 1];
    var waypoints = '';
    for (var i = 1; i < steps.length - 1; i++) {
      waypoints += (waypoints ? '|' : '') + steps[i].x + ',' + steps[i].y;
    }
    var contr = []
    Object.keys(this.get('constraint') || {}).forEach(function(c) {
      contr.push('{%22constraintType%22:%22banned%22,%22key%22:%22wayType%22,%22operator%22:%22=%22,%22value%22:%22'+c+'%22}')
    })
    return {
      resource: 'bdtopo-osrm',
      profile: this.get('mode') === 'pedestrian' ? 'pedestrian' : 'car',
      optimization: this.get('mode') === 'pedestrian' ? '' : this.get('method') || 'fastest',
      start: start.x + ',' + start.y,
      end: end.x + ',' + end.y,
      intermediates: waypoints,
      constraints: contr.join('|'),
      distanceUnit: 'meter',
      timeUnit: 'minute',
      geometryFormat: 'geojson'
    };
  }
  /** Gets time as string
   * @param {*} routing routing response
   * @return {string}
   * @api
   */
  getTimeString(t) {
    t /= 60;
    return (t < 1) ? '' : (t < 60) ? t.toFixed(0) + ' min' : (t / 60).toFixed(0) + ' h ' + (t % 60).toFixed(0) + ' min';
  }
  /** Gets distance as string
   * @param {number} d distance
   * @return {string}
   * @api
   */
  getDistanceString(d) {
    return (d < 1000) ? d.toFixed(0) + ' m' : (d / 1000).toFixed(2) + ' km';
  }
  /** Show routing as a list
   * @private
   */
  listRouting(routing) {
    this.resultElement.innerHTML = '';
    var t = this.getTimeString(routing.duration);
    t += ' (' + this.getDistanceString(routing.distance) + ')';
    var iElement = document.createElement('i');
    iElement.textContent = t;
    this.resultElement.appendChild(iElement);

    var ul = document.createElement('ul');
    this.resultElement.appendChild(ul);

    var infoType = ol_control_RoutingGeoportail.prototype.instructions[this.get('lang') || 'en']
    var infoClassName = {
      'straight': '',
      'left': 'L',
      'right': 'R',
      'slight left': 'FL',
      'slight right': 'FR',
    }

    routing.features.forEach(function (f, i) {
      var d = this.getDistanceString(f.get('distance'));
      t = this.getTimeString(f.get('durationT'));
      // Decode instructions
      var instruction = infoType[f.get('instruction_type')] || infoType['none'];
      instruction += ' ' + (infoType[f.get('instruction_modifier')] || infoType.straight) + ' ';
      // console.log(f.get('instruction_type'), '-',f.get('instruction_modifier'))
      // Show info
      ol_ext_element.create('LI', {
        className: infoClassName[f.get('instruction_modifier')] || '',
        html: (instruction || '#')
          + ' ' + f.get('name')
          + '<i>' + d + (t ? ' - ' + t : '') + '</i>',
        on: {
          pointerenter: function () {
            this.dispatchEvent({ type: 'step:hover', hover: false, index: i, feature: f });
          }.bind(this),
          pointerleave: function () {
            this.dispatchEvent({ type: 'step:hover', hover: false, index: i, feature: f });
          }.bind(this)
        },
        click: function () {
          this.dispatchEvent({ type: 'step:select', index: i, feature: f });
        }.bind(this),
        parent: ul
      });
    }.bind(this));
  }
  /** Handle routing response
   * @private
   */
  handleResponse(data, start, end) {
    if (data.status === 'ERROR') {
      this.dispatchEvent({
        type: 'errror',
        status: '200',
        statusText: data.message
      });
      return;
    }
    // console.log(data)
    var routing = { type: 'routing' };
    routing.features = [];
    var distance = 0;
    var duration = 0;
    var f;
    var parser = new ol_format_GeoJSON();
    var lastPt;
    for (var i = 0, l; l = data.portions[i]; i++) {
      for (var j = 0, s; s = l.steps[j]; j++) {
        s.type = 'Feature';
        s.properties = s.attributes.name || s.attributes;
        s.properties.distance = s.distance;
        s.properties.duration = Math.round(s.duration * 60);
        // Route info
        if (s.instruction) {
          s.properties.instruction_type = s.instruction.type;
          s.properties.instruction_modifier = s.instruction.modifier;
        }
        // Distance / time
        distance += s.distance;
        duration += s.duration;
        s.properties.distanceT = Math.round(distance * 100) / 100;
        s.properties.durationT = Math.round(duration * 60);
        s.properties.name = s.properties.cpx_toponyme_route_nommee || s.properties.cpx_toponyme || s.properties.cpx_numero || s.properties.nom_1_droite || s.properties.nom_1_gauche || '';
        // TODO: BUG ?
        var lp = s.geometry.coordinates[s.geometry.coordinates.length - 1];
        if (lastPt && !ol_coordinate_equal(lp, s.geometry.coordinates[s.geometry.coordinates.length - 1])) {
          s.geometry.coordinates.unshift(lastPt);
        }
        lastPt = s.geometry.coordinates[s.geometry.coordinates.length - 1];
        //
        f = parser.readFeature(s, {
          featureProjection: this.getMap().getView().getProjection()
        });
        routing.features.push(f);
      }
    }
    routing.distance = parseFloat(data.distance);
    routing.duration = parseFloat(data.duration) / 60;

    // Full route
    var route = parser.readGeometry(data.geometry, {
      featureProjection: this.getMap().getView().getProjection()
    });
    routing.feature = new ol_Feature({
      geometry: route,
      start: this._search[0].getTitle(start),
      end: this._search[0].getTitle(end),
      distance: routing.distance,
      duration: routing.duration
    });

    // console.log(data, routing);
    this.dispatchEvent(routing);
    this.path = routing;
    return routing;
  }
  /** Abort request
   */
  abort() {
    // Abort previous request
    if (this._request) {
      this._request.abort();
      this._request = null;
      this.dispatchEvent({ type: 'abort' });
    }
  }
  /** Calculate route
   * @param {Array<ol.coordinate>|undefined} steps an array of steps in EPSG:4326, default use control input values
   * @return {boolean} true is a new request is send (more than 2 points to calculate)
   */
  calculate(steps) {
    this.resultElement.innerHTML = '';
    if (steps) {
      var convert = [];
      steps.forEach(function (s) {
        convert.push({ x: s[0], y: s[1] });
      });
      steps = convert;
    } else {
      steps = [];
      for (var i = 0; i < this._search.length; i++) {
        if (this._search[i].get('selection'))
          steps.push(this._search[i].get('selection'));
      }
    }
    if (steps.length < 2)
      return false;

    var start = steps[0];
    var end = steps[steps.length - 1];

    var data = this.requestData(steps);

    var url = encodeURI(this.get('url'));

    var parameters = '';
    for (var index in data) {
      parameters += (parameters) ? '&' : '?';
      if (data.hasOwnProperty(index))
        parameters += index + '=' + data[index];
    }

    var self = this;
    this.dispatchEvent({ type: 'routing:start' });
    this.ajax(url + parameters,
      function (resp) {
        if (resp.status >= 200 && resp.status < 400) {
          self.listRouting(self.handleResponse(JSON.parse(resp.response), start, end));
        } else {
          //console.log(url + parameters, arguments);
          this.dispatchEvent({ type: 'error', status: resp.status, statusText: resp.statusText });
        }
      }.bind(this),
      function (resp) {
        // console.log('ERROR', resp)
        this.dispatchEvent({ type: 'error', status: resp.status, statusText: resp.statusText });
      }.bind(this)
    );

    return true;
  }
  /** Send an ajax request (GET)
   * @param {string} url
   * @param {function} onsuccess callback
   * @param {function} onerror callback
   */
  ajax(url, onsuccess, onerror) {
    var self = this;

    // Abort previous request
    if (this._request) {
      this._request.abort();
    }

    // New request
    var ajax = this._request = new XMLHttpRequest();
    ajax.open('GET', url, true);
    ajax.timeout = this.get('timeout') || 20000;

    if (this._auth) {
      ajax.setRequestHeader("Authorization", "Basic " + this._auth);
    }
    this.element.classList.add('ol-searching');

    // Load complete
    ajax.onload = function () {
      self._request = null;
      self.element.classList.remove('ol-searching');
      onsuccess.call(self, this);
    };

    // Timeout
    ajax.ontimeout = function () {
      self._request = null;
      self.element.classList.remove('ol-searching');
      if (onerror)
        onerror.call(self, this);
    };

    // Oops, TODO do something ?
    ajax.onerror = function () {
      self._request = null;
      self.element.classList.remove('ol-searching');
      if (onerror)
        onerror.call(self, this);
    };

    // GO!
    ajax.send();
  }
}

/** Instructions labels  */
ol_control_RoutingGeoportail.prototype.instructions = {
  'en': {
    // Instruction type
    'none': 'Go ',
    'continue': 'Continue ',
    'new name': 'Continue ',
    'depart': 'Start',
    'arrive': 'Arrival',
    'turn': 'Turn',
    'fork': 'Fork',
    // Instruction modifier
    'straight': 'on',
    'left': 'left on',
    'right': 'right on',
    'slight left': 'slight left on',
    'slight right': 'slight right on',
  },
  'fr': {
    // Instruction type
    'none': 'Continuer ',
    'continue': 'Continuer ',
    'new name': 'Continuer ',
    'depart': 'Départ',
    'arrive': 'Arrivée',
    'turn': 'Tourner',
    'fork': 'Prendre',
    // Instruction modifier
    'straight': 'sur',
    'left': 'à gauche sur',
    'right': 'à droite sur',
    'slight left': 'légèrement à gauche sur',
    'slight right': 'légèrement à droite sur',
  }
};

export default ol_control_RoutingGeoportail
