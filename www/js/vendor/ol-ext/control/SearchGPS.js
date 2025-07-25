/*	Copyright (c) 2019 Jean-Marc VIGLINO, 
	released under the CeCILL-B license (French BSD license)
	(http://www.cecill.info/licences/Licence_CeCILL-B_V1-en.txt).
*/

import {transform as ol_proj_transform} from 'ol/proj.js'
import {toStringHDMS as ol_coordinate_toStringHDMS} from 'ol/coordinate.js';
import ol_Geolocation from 'ol/Geolocation.js'

import ol_control_Search from './Search.js'
import ol_ext_element from '../util/element.js'

/**
 * Search on GPS coordinate.
 *
 * @constructor
 * @extends {ol_control_Search}
 * @fires select
 * @param {Object=} Control options. 
 *  @param {string} [options.className] control class name
 *  @param {Element | string | undefined} [options.target] Specify a target if you want the control to be rendered outside of the map's viewport.
 *  @param {string | undefined} [options.label=search] Text label to use for the search button, default "search"
 *  @param {string | undefined} [options.labelGPS="Locate with GPS"] placeholder, default "Locate with GPS"
 *  @param {number | undefined} [options.typing=300] a delay on each typing to start searching (ms), default 300.
 *  @param {integer | undefined} [options.minLength=1] minimum length to start searching, default 1
 *  @param {integer | undefined} [options.maxItems=10] maximum number of items to display in the autocomplete list, default 10
 */
var ol_control_SearchGPS = class olcontrolSearchGPS extends ol_control_Search {
  constructor(options) {
    options = options || {};
    options.className = (options.className || '') + ' ol-searchgps';
    options.placeholder = options.placeholder || 'lon,lat';

    super(options);

    // Geolocation
    this.geolocation = new ol_Geolocation({
      projection: "EPSG:4326",
      trackingOptions: {
        maximumAge: 10000,
        enableHighAccuracy: true,
        timeout: 600000
      }
    });
    ol_ext_element.create('BUTTON', {
      className: 'ol-geoloc',
      title: options.labelGPS || 'Locate with GPS',
      parent: this.element,
      click: function () {
        this.geolocation.setTracking(true);
      }.bind(this)
    });

    // DMS switcher
    ol_ext_element.createSwitch({
      html: 'decimal',
      after: 'DMS',
      change: function (e) {
        if (e.target.checked) {
          this.element.classList.add('ol-dms');
        } else {
          this.element.classList.remove('ol-dms');
        }
      }.bind(this),
      parent: this.element
    });

    this._createForm();

    // Move list to the end
    var ul = this.element.querySelector("ul.autocomplete");
    this.element.appendChild(ul);

  }
  /** Set the input value in the form (for initialisation purpose)
   *	@param {Array<number>} [coord] if none get the map center
   *	@api
   */
  setInput(coord) {
    if (!coord) {
      if (!this.getMap()) return
      coord = this.getMap().getView().getCenter();
      coord = ol_proj_transform(coord, this.getMap().getView().getProjection(), 'EPSG:4326')
    }
    this.inputs_[0].value = coord[0];
    this.inputs_[1].value = coord[1];
    this._triggerCustomEvent('keyup', this.inputs_[0]);
  }
  /** Create input form
   * @private
   */
  _createForm() {

    // Value has change
    var onchange = function (e) {
      if (e.target.classList.contains('ol-dms')) {
        lon.value = (lond.value < 0 ? -1 : 1) * Number(lond.value) + Number(lonm.value) / 60 + Number(lons.value) / 3600;
        lon.value = (lond.value < 0 ? -1 : 1) * Math.round(lon.value * 10000000) / 10000000;
        lat.value = (latd.value < 0 ? -1 : 1) * Number(latd.value) + Number(latm.value) / 60 + Number(lats.value) / 3600;
        lat.value = (latd.value < 0 ? -1 : 1) * Math.round(lat.value * 10000000) / 10000000;
      }
      if (lon.value || lat.value) {
        this._input.value = lon.value + ',' + lat.value;
      } else {
        this._input.value = '';
      }
      if (!e.target.classList.contains('ol-dms')) {
        var s = ol_coordinate_toStringHDMS([Number(lon.value) || 0, Number(lat.value) || 0]);
        var c = s.replace(/(N|S)/g,'-').replace(/(E|W)/g, '').split('-');
        try {
          c[1] = c[1].trim().split(' ');
          lond.value = (/W/.test(s) ? -1 : 1) * parseInt(c[1][0]);
          lonm.value = parseInt(c[1][1] || 0);
          lons.value = parseInt(c[1][2] || 0);
          c[0] = c[0].trim().split(' ');
          latd.value = (/S/.test(s) ? -1 : 1) * parseInt(c[0][0]);
          latm.value = parseInt(c[0][1] || 0);
          lats.value = parseInt(c[0][2] || 0);
        } catch(e) { /* oops */ }
      }
      this.search();
    }.bind(this);

    function createInput(className, unit) {
      var input = ol_ext_element.create('INPUT', {
        className: className,
        type: 'number',
        step: 'any',
        lang: 'en',
        parent: div,
        on: {
          'change keyup': onchange
        }
      });
      if (unit) {
        ol_ext_element.create('SPAN', {
          className: 'ol-dms',
          html: unit,
          parent: div,
        });
      }
      return input;
    }

    // Longitude
    var div = ol_ext_element.create('DIV', {
      className: 'ol-longitude',
      parent: this.element
    });
    ol_ext_element.create('LABEL', {
      html: 'Longitude',
      parent: div
    });
    var lon = createInput('ol-decimal');
    var lond = createInput('ol-dms', '°');
    var lonm = createInput('ol-dms', '\'');
    var lons = createInput('ol-dms', '"');

    // Latitude
    div = ol_ext_element.create('DIV', {
      className: 'ol-latitude',
      parent: this.element
    });
    ol_ext_element.create('LABEL', {
      html: 'Latitude',
      parent: div
    });
    var lat = createInput('ol-decimal');
    var latd = createInput('ol-dms', '°');
    var latm = createInput('ol-dms', '\'');
    var lats = createInput('ol-dms', '"');

    this.inputs_ = [lon, lat]

    // Focus on open
    if (this.button) {
      this.button.addEventListener("click", function () {
        lon.focus();
      });
    }

    // Change value on click
    this.on('select', function (e) {
      lon.value = e.search.gps[0];
      lat.value = e.search.gps[1];
    }.bind(this));

    // Change value on geolocation
    this.geolocation.on('change', function () {
      this.geolocation.setTracking(false);
      var coord = this.geolocation.getPosition();
      lon.value = coord[0];
      lat.value = coord[1];
      this._triggerCustomEvent('keyup', lon);
    }.bind(this));
  }
  /** Autocomplete function
  * @param {string} s search string
  * @return {Array<any>|false} an array of search solutions
  * @api
  */
  autocomplete(s) {
    var result = [];
    var c = s.split(',');
    c[0] = Number(c[0]);
    c[1] = Number(c[1]);
    // Name
    s = ol_coordinate_toStringHDMS(c);
    if (s)
      s = s.replace(/(°|′|″) /g, '$1');
    // 
    var coord = ol_proj_transform([c[0], c[1]], 'EPSG:4326', this.getMap().getView().getProjection());
    result.push({ gps: c, coordinate: coord, name: s });
    return result;
  }
}

export default ol_control_SearchGPS