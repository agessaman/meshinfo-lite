/*	Copyright (c) 2016 Jean-Marc VIGLINO, 
  released under the CeCILL-B license (French BSD license)
  (http://www.cecill.info/licences/Licence_CeCILL-B_V1-en.txt).
*/

import {unByKey as ol_Observable_unByKey} from 'ol/Observable.js'
import ol_Collection from 'ol/Collection.js'
import ol_View from 'ol/View.js'
import ol_Overlay from 'ol/Overlay.js'
import ol_Map from 'ol/Map.js'

/**
 * @classdesc
 *	The Magnify overlay add a "magnifying glass" effect to an OL3 map that displays 
*	a portion of the map in a different zoom (and actually display different content).
*
* @constructor
* @extends {ol_Overlay}
* @param {olx.OverlayOptions} options Overlay options 
* @api stable
*/
var ol_Overlay_Magnify = class olOverlayMagnify extends ol_Overlay {
  constructor(options) {
    var elt = document.createElement("div")
    elt.className = "ol-magnify"
    
    super({
      positioning: options.positioning || "center-center",
      element: elt,
      stopEvent: false
    })
    this._elt = elt

    // Create magnify map
    this.mgmap_ = new ol_Map({
      controls: new ol_Collection(),
      interactions: new ol_Collection(),
      target: options.target || this._elt,
      view: new ol_View({ projection: options.projection }),
      layers: options.layers
    })
    this.mgview_ = this.mgmap_.getView()

    this.external_ = options.target ? true : false

    this.set("zoomOffset", options.zoomOffset || 1)
    this.set("active", true)
    this.on("propertychange", this.setView_.bind(this))
  }
  /**
   * Set the map instance the overlay is associated with.
   * @param {ol.Map} map The map instance.
   */
  setMap(map) {
    if (this.getMap()) {
      this.getMap().getViewport().removeEventListener("mousemove", this.onMouseMove_)
    }
    if (this._listener) ol_Observable_unByKey(this._listener)
    this._listener = null

    super.setMap(map)
    map.getViewport().addEventListener("mousemove", this.onMouseMove_.bind(this))
    this._listener = map.getView().on('propertychange', this.setView_.bind(this))
    this.refresh()
  }
  /** Get the magnifier map
  *	@return {_ol_Map_}
  */
  getMagMap() {
    return this.mgmap_
  }
  /** Magnify is active
  *	@return {boolean}
  */
  getActive() {
    return this.get("active")
  }
  /** Activate or deactivate
  *	@param {boolean} active
  */
  setActive(active) {
    this.set("active", active)
    this.refresh();
    return this.getActive()
  }
  /** Mouse move
   * @private
   */
  onMouseMove_(e) {
    if (!this.get("active")) {
      this.setPosition()
    } else {
      var isPosition = this.getPosition()
      var px = this.getMap().getEventCoordinate(e)
      if (!this.external_) {
        this.setPosition(px)
      }
      this.mgview_.setCenter(px)
      /*
      if (!this._elt.querySelector('canvas') || this._elt.querySelector('canvas').style.display === "none"){
        this.mgmap_.updateSize()
      }
      */
      if (!this.external_ && !isPosition) {
        this.refresh()
      }
    }
  }
  /** Refresh the view
   */
  refresh() {
    this.mgmap_.updateSize()
    this.setView_();
  }
  /** View has changed
   * @private
   */
  setView_(e) {
    // No map
    if (!this.getMap()) return
    // Not active
    if (!this.get("active")) {
      this.setPosition()
      return
    }

    if (!e) {
      // refresh all
      this.setView_({ key: 'rotation' })
      this.setView_({ key: 'resolution' })
      return
    }

    // Set the view params
    switch (e.key) {
      case 'rotation':
        this.mgview_.setRotation(this.getMap().getView().getRotation())
        break
      case 'zoomOffset':
      case 'resolution': {
        var z = Math.max(0, this.getMap().getView().getZoom() + Number(this.get("zoomOffset")))
        this.mgview_.setZoom(z)
        break
      }
      default: break
    }
  }
}

export default ol_Overlay_Magnify
