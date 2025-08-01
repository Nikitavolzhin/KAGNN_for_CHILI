import torch.nn as nn
import torch.nn.functional as F
from ekan import KAN, KANLinear

from torch_geometric.nn import GINEConv, GCNConv, GINConv
from torch_geometric.nn import global_add_pool, global_mean_pool
from torch_geometric.nn.conv import EdgeConv


# The following function: make_kan, KAGIN, KANLayer, KAGCN_Layer, KAGCN, AtomEncoder, get_atom_feature_dims,
# BondEncoder and get_bond_feature_dims; are from https://github.com/RomanBresson/KAGNN

# KAGIN and KAGCN were modified for compatibility with node-level tasks
# KAEdge and get_KAEdgeCNN_layer were built using the same logic as https://github.com/RomanBresson/KAGNN
def make_kan(num_features, hidden_dim, out_dim, hidden_layers, grid_size, spline_order):
    sizes = [num_features] + [hidden_dim] * (hidden_layers - 1) + [out_dim]
    return (KAN(layers_hidden=sizes, grid_size=grid_size, spline_order=spline_order))

def get_KAEdgeCNN_layer(in_channels: int, out_channels: int, grid_size:int, spline_order:int,
                  **kwargs):
    if grid_size is None:
        raise ValueError("grid size must be provided")
    if spline_order is None:
        raise ValueError("spline order must be provided")
    kan = make_kan(
        num_features=2 * in_channels,
        hidden_dim=out_channels,
        out_dim=out_channels,
        hidden_layers=1,
        grid_size=grid_size,
        spline_order=spline_order
    )
    return EdgeConv(kan, **kwargs)

class KAEdge(nn.Module):
    def __init__(self, num_node_features, num_edge_features, gnn_layers, hidden_dim, hidden_layers, grid_size,
                 spline_order, num_classes, dropout):
        super(KAEdge, self).__init__()
        self.n_layers = gnn_layers
        lst = []
        lst.append(get_KAEdgeCNN_layer(in_channels=num_node_features, out_channels=hidden_dim, grid_size=grid_size,
                                           spline_order=spline_order))
        for _ in range(gnn_layers-1):
            lst.append(get_KAEdgeCNN_layer(in_channels=hidden_dim, out_channels=hidden_dim, grid_size=grid_size, spline_order=spline_order))
        self.conv = nn.ModuleList(lst)
        lst = list()
        for i in range(gnn_layers):
            lst.append(nn.BatchNorm1d(hidden_dim))
        self.bn = nn.ModuleList(lst)
        self.kan = make_kan(hidden_dim, hidden_dim, num_classes, hidden_layers, grid_size, spline_order)
        self.dropout = nn.Dropout(dropout)
        self.lay_out = KANLinear(hidden_dim, num_classes, grid_size=grid_size, spline_order=spline_order)

    def forward(self, x, edge_index, batch, edge_attr=None, edge_weight=None, node_level: bool = True):

        for i in range(self.n_layers):
            x = self.conv[i](x, edge_index)
            x = self.bn[i](x)
            x = F.silu(x)
            x = self.dropout(x)
        if node_level:
            x = self.lay_out(x)
            return x
        x = global_add_pool(x, batch)
        x = self.kan(x)
        return x

class KAGIN(nn.Module):
    def __init__(self, num_node_features, num_edge_features, gnn_layers, hidden_dim, hidden_layers, grid_size,
                 spline_order, num_classes, dropout, ogb_encoders):
        super(KAGIN, self).__init__()
        self.n_layers = gnn_layers
        if ogb_encoders:
            self.atom_encoder = AtomEncoder(hidden_dim)
            self.bond_encoder = BondEncoder(hidden_dim)
        else:
            self.atom_encoder = nn.Linear(num_node_features, hidden_dim)
            self.bond_encoder = nn.Linear(num_edge_features, hidden_dim)
        lst = []
        for _ in range(gnn_layers):
            lst.append(GINConv(make_kan(hidden_dim, hidden_dim, hidden_dim, hidden_layers, grid_size, spline_order)))
        self.conv = nn.ModuleList(lst)
        lst = list()
        for i in range(gnn_layers):
            lst.append(nn.BatchNorm1d(hidden_dim))
        self.bn = nn.ModuleList(lst)
        self.kan = make_kan(hidden_dim, hidden_dim, num_classes, hidden_layers, grid_size, spline_order)
        self.dropout = nn.Dropout(dropout)
        self.lay_out = KANLinear(hidden_dim, num_classes, grid_size=grid_size, spline_order=spline_order)

    def forward(self, x, edge_index, batch, edge_attr=None, edge_weight=None, node_level: bool = True):

        x = self.atom_encoder(x)
        for i in range(self.n_layers):
            x = self.conv[i](x, edge_index)
            x = self.bn[i](x)
            x = self.dropout(x)
        if node_level:
            x = self.lay_out(x)
            return x
        x = global_add_pool(x, batch)
        x = self.kan(x)
        return x

class KANLayer(KANLinear):
    def __init__(self, input_dim, output_dim, grid_size=4, spline_order=3):
        super(KANLayer, self).__init__(in_features=input_dim, out_features=output_dim, grid_size=grid_size,
                                       spline_order=spline_order)


class KAGCN_Layer(GCNConv):
    def __init__(self, in_feat: int,
                 out_feat: int,
                 grid_size: int = 4,
                 spline_order: int = 3):
        super(KAGCN_Layer, self).__init__(in_feat, out_feat)
        self.lin = KANLayer(in_feat, out_feat, grid_size, spline_order)


class KAGCN(nn.Module):
    def __init__(self, num_node_features, gnn_layers, hidden_dim, grid_size, spline_order, num_classes, dropout,
                 ogb_encoders):
        super(KAGCN, self).__init__()
        self.n_layers = gnn_layers
        if ogb_encoders:
            self.atom_encoder = AtomEncoder(hidden_dim)
        else:
            self.atom_encoder = nn.Linear(num_node_features, hidden_dim)
        lst = []
        for _ in range(gnn_layers):
            lst.append(KAGCN_Layer(hidden_dim, hidden_dim))
        self.conv = nn.ModuleList(lst)
        self.readout = make_kan(hidden_dim, hidden_dim, num_classes, 1, grid_size, spline_order)
        self.dropout = nn.Dropout(p=dropout)
        self.lay_out = KANLinear(hidden_dim, num_classes, grid_size=grid_size, spline_order=spline_order)

    def forward(self, x, edge_index, batch, edge_attr=None, edge_weight=None, node_level: bool = True):

        x = self.atom_encoder(x)
        for i in range(self.n_layers):
            x = self.conv[i](x, edge_index)
            x = F.silu(x)
            x = self.dropout(x)
        if node_level:
            x = self.lay_out(x)
            return x
        x = global_add_pool(x, batch)
        x = self.readout(x)
        return x

class AtomEncoder(nn.Module):
    def __init__(self, emb_dim, optional_full_atom_features_dims=None):
        super(AtomEncoder, self).__init__()
        self.atom_embedding_list = nn.ModuleList()
        if optional_full_atom_features_dims is not None:
            full_atom_feature_dims = optional_full_atom_features_dims
        else:
            full_atom_feature_dims = get_atom_feature_dims()

        for i, dim in enumerate(full_atom_feature_dims):
            emb = nn.Embedding(dim, emb_dim)
            nn.init.xavier_uniform_(emb.weight.data)
            self.atom_embedding_list.append(emb)

    def forward(self, x):
        x_embedding = 0
        for i in range(x.shape[1]):
            x_embedding += self.atom_embedding_list[i](x[:, i])
        return x_embedding


class BondEncoder(nn.Module):
    def __init__(self, emb_dim):
        super(BondEncoder, self).__init__()
        full_bond_feature_dims = get_bond_feature_dims()
        self.bond_embedding_list = nn.ModuleList()

        for i, dim in enumerate(full_bond_feature_dims):
            emb = nn.Embedding(dim, emb_dim)
            nn.init.xavier_uniform_(emb.weight.data)
            self.bond_embedding_list.append(emb)

    def forward(self, edge_attr):
        bond_embedding = 0
        for i in range(edge_attr.shape[1]):
            bond_embedding += self.bond_embedding_list[i](edge_attr[:, i])
        return bond_embedding

    # allowable multiple choice node and edge features


allowable_features = {
    'possible_atomic_num_list': list(range(1, 119)) + ['misc'],
    'possible_chirality_list': [
        'CHI_UNSPECIFIED',
        'CHI_TETRAHEDRAL_CW',
        'CHI_TETRAHEDRAL_CCW',
        'CHI_OTHER',
        'misc'
    ],
    'possible_degree_list': [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 'misc'],
    'possible_formal_charge_list': [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 'misc'],
    'possible_numH_list': [0, 1, 2, 3, 4, 5, 6, 7, 8, 'misc'],
    'possible_number_radical_e_list': [0, 1, 2, 3, 4, 'misc'],
    'possible_hybridization_list': [
        'SP', 'SP2', 'SP3', 'SP3D', 'SP3D2', 'misc'
    ],
    'possible_is_aromatic_list': [False, True],
    'possible_is_in_ring_list': [False, True],
    'possible_bond_type_list': [
        'SINGLE',
        'DOUBLE',
        'TRIPLE',
        'AROMATIC',
        'misc'
    ],
    'possible_bond_stereo_list': [
        'STEREONONE',
        'STEREOZ',
        'STEREOE',
        'STEREOCIS',
        'STEREOTRANS',
        'STEREOANY',
    ],
    'possible_is_conjugated_list': [False, True],
}


def get_atom_feature_dims():
    return list(map(len, [
        allowable_features['possible_atomic_num_list'],
        allowable_features['possible_chirality_list'],
        allowable_features['possible_degree_list'],
        allowable_features['possible_formal_charge_list'],
        allowable_features['possible_numH_list'],
        allowable_features['possible_number_radical_e_list'],
        allowable_features['possible_hybridization_list'],
        allowable_features['possible_is_aromatic_list'],
        allowable_features['possible_is_in_ring_list']
    ]))


def get_bond_feature_dims():
    return list(map(len, [
        allowable_features['possible_bond_type_list'],
        allowable_features['possible_bond_stereo_list'],
        allowable_features['possible_is_conjugated_list']
    ]))